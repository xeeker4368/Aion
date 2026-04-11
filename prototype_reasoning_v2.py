"""
Prototype v2: LLM Reasoning Step for Retrieval + Tool Intent

CHANGES FROM v1:
- Reasoning instruction rewritten to focus on vocabulary matching
- Added explicit examples of good vs bad queries
- Greeting/SKIP instruction made clearer
- Tool names made explicit (web_search, web_fetch)
- Added a second run of Test 1 and 2 for consistency check

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_reasoning_v2.py
"""

import sqlite3
import sys
from pathlib import Path

# ============================================================
# Path detection
# ============================================================

CANDIDATES = [
    Path(__file__).parent,
    Path.home() / "aion",
    Path("/home/localadmin/aion"),
]

AION_DIR = None
for candidate in CANDIDATES:
    if (candidate / "soul.md").exists() and (candidate / "server.py").exists():
        AION_DIR = candidate
        break

if AION_DIR is None:
    print("ERROR: Cannot find the aion project directory.")
    sys.exit(1)

sys.path.insert(0, str(AION_DIR))

import ollama
import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

# ============================================================
# Configuration
# ============================================================

OLLAMA_HOST = "http://localhost:11434"
CHAT_MODEL = "llama3.1:8b-aion"
EMBED_MODEL = "nomic-embed-text"
CONTEXT_WINDOW = 10240
RETRIEVAL_MAX_DISTANCE = 0.75
RETRIEVAL_RESULTS = 5

WORKING_DB = AION_DIR / "data" / "prod" / "working.db"
CHROMA_DIR = str(AION_DIR / "data" / "prod" / "chromadb")
SOUL_PATH = AION_DIR / "soul.md"

TARGET_CONV_PREFIX = "33158f80"
RELAY_CONV_PREFIX = "bfc118df"


# ============================================================
# Database and ChromaDB
# ============================================================

def connect_db():
    if not WORKING_DB.exists():
        print(f"ERROR: Database not found at {WORKING_DB}")
        sys.exit(1)
    conn = sqlite3.connect(str(WORKING_DB))
    conn.row_factory = sqlite3.Row
    return conn


def connect_chroma():
    if not Path(CHROMA_DIR).exists():
        print(f"ERROR: ChromaDB not found at {CHROMA_DIR}")
        sys.exit(1)
    embedding_fn = OllamaEmbeddingFunction(
        url=OLLAMA_HOST,
        model_name=EMBED_MODEL,
    )
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = client.get_or_create_collection(
        name="aion_memory",
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def load_soul():
    if not SOUL_PATH.exists():
        return ""
    return SOUL_PATH.read_text()


def load_self_knowledge(conn):
    row = conn.execute(
        "SELECT content FROM self_knowledge ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return row["content"] if row else ""


def load_conversation(conn, conv_prefix):
    row = conn.execute(
        "SELECT id FROM conversations WHERE id LIKE ?",
        (f"{conv_prefix}%",),
    ).fetchone()
    if not row:
        return None, []
    full_id = row["id"]
    messages = conn.execute(
        "SELECT role, content, timestamp FROM messages "
        "WHERE conversation_id = ? ORDER BY timestamp",
        (full_id,),
    ).fetchall()
    return full_id, [dict(m) for m in messages]


def format_timestamp(ts):
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%B %d, %Y at %I:%M %p")
    except Exception:
        return ts


# ============================================================
# ChromaDB search
# ============================================================

def search_chromadb(collection, query, n_results=RETRIEVAL_RESULTS):
    trust_weights = {
        "firsthand": 0.9,
        "secondhand": 1.0,
        "thirdhand": 1.1,
    }
    fetch_count = min(n_results * 3, collection.count())
    try:
        results = collection.query(
            query_texts=[query],
            n_results=fetch_count,
        )
    except Exception as e:
        print(f"  ChromaDB search error: {e}")
        return []

    if not results or not results["documents"] or not results["documents"][0]:
        return []

    memories = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        raw_distance = results["distances"][0][i] if results["distances"] else None
        trust = meta.get("source_trust", "firsthand")
        weight = trust_weights.get(trust, 1.0)
        weighted = raw_distance * weight if raw_distance is not None else None
        memories.append({
            "text": doc,
            "conversation_id": meta.get("conversation_id", ""),
            "distance": raw_distance,
            "weighted_distance": weighted,
            "source_type": meta.get("source_type", ""),
            "source_trust": meta.get("source_trust", ""),
        })

    memories.sort(
        key=lambda m: m["weighted_distance"]
        if m["weighted_distance"] is not None else float("inf")
    )

    seen = set()
    deduped = []
    for mem in memories:
        if mem["weighted_distance"] and mem["weighted_distance"] > RETRIEVAL_MAX_DISTANCE:
            break
        cid = mem["conversation_id"]
        if cid not in seen:
            seen.add(cid)
            deduped.append(mem)
        if len(deduped) >= n_results:
            break
    return deduped


# ============================================================
# The reasoning step — v2
# ============================================================

REASONING_INSTRUCTION = """You are about to respond to a new message. First, decide what you need.

MEMORIES: If this message relates to something from your past, you need to search your memory. Your memories are stored as chunks of actual conversations — the real words that were said. Think about what words and phrases were actually used in those conversations and write a search using those words. Do not describe the topic abstractly. Use the specific words that would appear in the conversation you are trying to find.

TOOLS: If this message asks you to look something up on the web, say exactly: TOOL: web_search
If it asks you to read a URL, say exactly: TOOL: web_fetch

SKIP: If this is just a greeting, a thank you, or casual small talk that does not need memories or tools, say exactly: SKIP

Respond with ONLY your search words, or TOOL: web_search, or TOOL: web_fetch, or SKIP. Nothing else. No explanation."""


def build_reasoning_context(conversation_messages, current_message,
                            self_knowledge, soul):
    history_lines = []
    for msg in conversation_messages:
        ts = format_timestamp(msg["timestamp"])
        history_lines.append(f"[{ts}] {msg['role']}: {msg['content']}")
    history_text = "\n".join(history_lines)

    system_parts = []
    if history_text:
        system_parts.append(
            f"Here is the conversation you are currently having:\n\n{history_text}"
        )
    if self_knowledge:
        system_parts.append(
            f"\nWhat you have learned about yourself through experience:\n\n"
            f"{self_knowledge}"
        )
    system_parts.append(f"\n{soul}")
    system_prompt = "\n".join(system_parts)

    user_content = (
        f"New message from the user: \"{current_message}\"\n\n"
        f"{REASONING_INSTRUCTION}"
    )

    return system_prompt, user_content


def call_reasoning(system_prompt, user_content):
    client = ollama.Client(host=OLLAMA_HOST)
    response = client.chat(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        options={"num_ctx": CONTEXT_WINDOW},
    )
    return response["message"]["content"].strip()


def parse_reasoning_output(output):
    text = output.strip()
    lower = text.lower()

    if lower == "skip" or lower == "skip.":
        return "skip", None

    if lower.startswith("tool:"):
        return "tool", text[5:].strip()

    # Treat as search query — clean up
    query = text
    for prefix in [
        "search query:", "search:", "query:", "memory search:",
        "search for:", "look for:", "find:", "memories:",
        "memory:", "memory_retrieval:", "mem:",
    ]:
        if query.lower().startswith(prefix):
            query = query[len(prefix):].strip()
    query = query.strip("\"'")

    return "search", query


# ============================================================
# Test runner
# ============================================================

def run_test(collection, test_name, conv_messages, user_message,
             self_knowledge, soul, run_number=1):
    print(f"\n{'=' * 70}")
    print(f"TEST: {test_name}" + (f" (run {run_number})" if run_number > 1 else ""))
    print(f"User message: \"{user_message[:100]}\"")
    print(f"Conversation history: {len(conv_messages)} messages")
    print(f"{'=' * 70}")

    # --- Current system ---
    if run_number == 1:
        print(f"\n--- CURRENT SYSTEM (raw embedding of user message) ---")
        raw_results = search_chromadb(collection, user_message)
        has_relay_raw = False
        if raw_results:
            for i, r in enumerate(raw_results):
                marker = ""
                if r["conversation_id"].startswith(RELAY_CONV_PREFIX):
                    marker = " *** RELAY ***"
                    has_relay_raw = True
                preview = r["text"][:100].replace("\n", " ")
                print(f"  {i+1}. [{r['weighted_distance']:.4f}] "
                      f"{r['source_type']}/{r['conversation_id'][:8]}..."
                      f" {preview}{marker}")
        else:
            print("  (no results)")
    else:
        has_relay_raw = False  # skip re-running raw for consistency runs

    # --- Reasoning step ---
    print(f"\n--- REASONING STEP ---")
    system_prompt, user_content = build_reasoning_context(
        conv_messages, user_message, self_knowledge, soul,
    )
    total_chars = len(system_prompt) + len(user_content)
    print(f"  Context size: ~{total_chars // 4} tokens")

    reasoning_output = call_reasoning(system_prompt, user_content)
    print(f"  Raw model output: \"{reasoning_output}\"")

    decision, value = parse_reasoning_output(reasoning_output)
    print(f"  Decision: {decision}" + (f" — {value}" if value else ""))

    # --- Search with generated query ---
    has_relay_reasoning = False
    if decision == "search" and value:
        print(f"\n--- PROPOSED SYSTEM (LLM-generated query) ---")
        print(f"  Query: \"{value}\"")
        reasoning_results = search_chromadb(collection, value)
        if reasoning_results:
            for i, r in enumerate(reasoning_results):
                marker = ""
                if r["conversation_id"].startswith(RELAY_CONV_PREFIX):
                    marker = " *** RELAY ***"
                    has_relay_reasoning = True
                preview = r["text"][:100].replace("\n", " ")
                print(f"  {i+1}. [{r['weighted_distance']:.4f}] "
                      f"{r['source_type']}/{r['conversation_id'][:8]}..."
                      f" {preview}{marker}")
        else:
            print("  (no results)")

        if run_number == 1:
            print(f"\n--- RESULT ---")
            print(f"  Raw embedding found relay chunks:   {has_relay_raw}")
            print(f"  LLM reasoning found relay chunks:   {has_relay_reasoning}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v2: LLM Reasoning Step for Retrieval + Tool Intent")
    print("=" * 70)

    conn = connect_db()
    collection = connect_chroma()
    print(f"Project dir:  {AION_DIR}")
    print(f"ChromaDB:     {collection.count()} chunks")

    soul = load_soul()
    self_knowledge = load_self_knowledge(conn)
    print(f"SOUL.md:      ~{len(soul)//4} tokens")
    print(f"Self-knowledge: ~{len(self_knowledge)//4} tokens")

    full_id, all_messages = load_conversation(conn, TARGET_CONV_PREFIX)
    if not all_messages:
        sys.exit(1)
    print(f"Conversation: {full_id[:8]}... ({len(all_messages)} messages)")

    # ============================================================
    # Test 1: "most important difference between you two"
    # Run 3 times to check consistency
    # ============================================================
    for run in range(1, 4):
        run_test(
            collection,
            test_name='Claude difference — "most important difference between you two"',
            conv_messages=all_messages[:4],
            user_message=all_messages[4]["content"],
            self_knowledge=self_knowledge,
            soul=soul,
            run_number=run,
        )

    # ============================================================
    # Test 2: "summarize everything claude told you yesterday"
    # Run 3 times
    # ============================================================
    for run in range(1, 4):
        run_test(
            collection,
            test_name='Summarize Claude — "summarize everything claude told you yesterday"',
            conv_messages=all_messages[:12],
            user_message=all_messages[12]["content"],
            self_knowledge=self_knowledge,
            soul=soul,
            run_number=run,
        )

    # ============================================================
    # Test 3: Greeting — should SKIP
    # ============================================================
    run_test(
        collection,
        test_name="Greeting — should produce SKIP",
        conv_messages=[],
        user_message="Good evening Nyx",
        self_knowledge=self_knowledge,
        soul=soul,
    )

    # ============================================================
    # Test 4: Web search — should produce TOOL: web_search
    # ============================================================
    run_test(
        collection,
        test_name="Web search — should produce TOOL: web_search",
        conv_messages=all_messages[:4],
        user_message="Can you search the web for recent news about AI consciousness research?",
        self_knowledge=self_knowledge,
        soul=soul,
    )

    # ============================================================
    # Test 5: Name identity
    # ============================================================
    run_test(
        collection,
        test_name="Name identity — should find naming conversations",
        conv_messages=[],
        user_message="Do you remember how you chose your name?",
        self_knowledge=self_knowledge,
        soul=soul,
    )

    print(f"\n{'=' * 70}")
    print("PROTOTYPE v2 COMPLETE")
    print(f"{'=' * 70}")
    conn.close()


if __name__ == "__main__":
    main()
