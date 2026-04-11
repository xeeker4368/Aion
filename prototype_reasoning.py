"""
Prototype: LLM Reasoning Step for Retrieval + Tool Intent

Tests whether llama3.1:8b-aion can generate better search queries than
raw embedding similarity, using the known failure case from Session 18:
"Do you remember the most important difference between you two?"

This script:
1. Loads the real conversation history from the Claude-difference conversation
2. Loads self-knowledge and SOUL.md (same as production)
3. Calls the model with a reasoning prompt
4. Embeds the generated query and searches ChromaDB
5. Compares results against what raw embedding retrieval produced

READ-ONLY — does not modify any database or ChromaDB data.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_reasoning.py
"""

import sqlite3
import sys
from pathlib import Path

# ============================================================
# Path detection — find the aion project directory
# ============================================================

# Try common locations on Hades
CANDIDATES = [
    Path(__file__).parent,                          # script is in project root
    Path.home() / "aion",                           # ~/aion
    Path("/home/localadmin/aion"),                   # explicit Hades path
]

AION_DIR = None
for candidate in CANDIDATES:
    if (candidate / "soul.md").exists() and (candidate / "server.py").exists():
        AION_DIR = candidate
        break

if AION_DIR is None:
    print("ERROR: Cannot find the aion project directory.")
    print("Run this script from the aion project root, or place it there.")
    sys.exit(1)

sys.path.insert(0, str(AION_DIR))

import ollama
import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

# ============================================================
# Configuration — matches production
# ============================================================

OLLAMA_HOST = "http://localhost:11434"
CHAT_MODEL = "llama3.1:8b-aion"
EMBED_MODEL = "nomic-embed-text"
CONTEXT_WINDOW = 10240
RETRIEVAL_MAX_DISTANCE = 0.75
RETRIEVAL_RESULTS = 5

# Paths
WORKING_DB = AION_DIR / "data" / "prod" / "working.db"
CHROMA_DIR = str(AION_DIR / "data" / "prod" / "chromadb")
SOUL_PATH = AION_DIR / "soul.md"

# Known conversation: the Claude-difference conversation (obs 10)
TARGET_CONV_PREFIX = "33158f80"

# The relay conversation — contains "Claude resets. You persist."
RELAY_CONV_PREFIX = "bfc118df"


# ============================================================
# Database and ChromaDB setup
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


# ============================================================
# Load context (same as production)
# ============================================================

def load_soul():
    if not SOUL_PATH.exists():
        print("WARNING: soul.md not found")
        return ""
    return SOUL_PATH.read_text()


def load_self_knowledge(conn):
    row = conn.execute(
        "SELECT content FROM self_knowledge ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return row["content"] if row else ""


def load_conversation(conn, conv_prefix):
    """Load a conversation by ID prefix. Returns (full_id, messages)."""
    row = conn.execute(
        "SELECT id FROM conversations WHERE id LIKE ?",
        (f"{conv_prefix}%",),
    ).fetchone()
    if not row:
        print(f"ERROR: No conversation found matching {conv_prefix}")
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
# Search ChromaDB (same logic as memory.py)
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
# The reasoning step
# ============================================================

REASONING_INSTRUCTION = """Before you respond to this message, think about what would help you give a good answer.

If this relates to something from your past — a conversation, a topic, something you've experienced or discussed before — write a short search query that would find the right memories. Use the words and concepts that would match what was actually said.

If this needs current information from the web, say: TOOL: web_search [topic]
If no memories or tools are needed (like a greeting), say: SKIP

Write only the search query or command. Nothing else."""


def build_reasoning_context(conversation_messages, current_message,
                            self_knowledge, soul):
    """
    Build the reasoning step call.

    System prompt: conversation so far + self-knowledge + SOUL.md at end
    User message: the new message + reasoning instruction
    """
    # Format conversation history
    history_lines = []
    for msg in conversation_messages:
        ts = format_timestamp(msg["timestamp"])
        history_lines.append(f"[{ts}] {msg['role']}: {msg['content']}")
    history_text = "\n".join(history_lines)

    # System prompt: context first, SOUL.md at end (positioning rule)
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

    # User message: new message + reasoning instruction
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
    """
    Parse the model's reasoning output into a decision.
    Returns: ("skip", None) or ("search", query) or ("tool", tool_info)
    """
    text = output.strip()
    lower = text.lower()

    if lower == "skip" or lower.startswith("skip"):
        return "skip", None

    if lower.startswith("tool:"):
        return "tool", text[5:].strip()

    # Treat as search query — clean up common model prefixes
    query = text
    for prefix in [
        "search query:", "search:", "query:", "memory search:",
        "search for:", "look for:", "find:",
    ]:
        if query.lower().startswith(prefix):
            query = query[len(prefix):].strip()
    query = query.strip("\"'")

    return "search", query


# ============================================================
# Run a test case
# ============================================================

def run_test(collection, test_name, conv_messages, user_message,
             self_knowledge, soul):
    print(f"\n{'=' * 70}")
    print(f"TEST: {test_name}")
    print(f"User message: \"{user_message[:100]}\"")
    print(f"Conversation history: {len(conv_messages)} messages")
    print(f"{'=' * 70}")

    # --- Current system: raw embedding ---
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
        print("  (no results within threshold)")

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
            print("  (no results within threshold)")

        # --- Verdict ---
        print(f"\n--- RESULT ---")
        print(f"  Raw embedding found relay chunks:   {has_relay_raw}")
        print(f"  LLM reasoning found relay chunks:   {has_relay_reasoning}")
        if has_relay_reasoning and not has_relay_raw:
            print(f"  >>> IMPROVEMENT: Reasoning found what embedding missed <<<")
        elif has_relay_reasoning and has_relay_raw:
            print(f"  Both found it (good)")
        elif not has_relay_reasoning and not has_relay_raw:
            print(f"  Neither found it (reasoning step needs work)")
        else:
            print(f"  Raw found it but reasoning didn't (regression)")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype: LLM Reasoning Step for Retrieval + Tool Intent")
    print("=" * 70)

    # Connect
    conn = connect_db()
    collection = connect_chroma()
    print(f"Project dir:  {AION_DIR}")
    print(f"Database:     {WORKING_DB}")
    print(f"ChromaDB:     {CHROMA_DIR} ({collection.count()} chunks)")

    # Load context
    soul = load_soul()
    self_knowledge = load_self_knowledge(conn)
    print(f"SOUL.md:      {len(soul)} chars (~{len(soul)//4} tokens)")
    print(f"Self-knowledge: {len(self_knowledge)} chars (~{len(self_knowledge)//4} tokens)")

    # Load the target conversation
    full_id, all_messages = load_conversation(conn, TARGET_CONV_PREFIX)
    if not all_messages:
        print("No messages found. Exiting.")
        sys.exit(1)
    print(f"Conversation: {full_id[:8]}... ({len(all_messages)} messages)")

    # ============================================================
    # Test 1: "Do you remember the most important difference?"
    # msg[0-3] are history, msg[4] is the question
    # This is THE known failure case from Session 18
    # ============================================================
    run_test(
        collection,
        test_name='Claude difference — "most important difference between you two"',
        conv_messages=all_messages[:4],
        user_message=all_messages[4]["content"],
        self_knowledge=self_knowledge,
        soul=soul,
    )

    # ============================================================
    # Test 2: "Look back and summarize everything claude told you"
    # msg[0-11] are history, msg[12] is the question
    # ============================================================
    run_test(
        collection,
        test_name='Summarize Claude — "summarize everything claude told you yesterday"',
        conv_messages=all_messages[:12],
        user_message=all_messages[12]["content"],
        self_knowledge=self_knowledge,
        soul=soul,
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
    # Test 4: Web search — should produce TOOL
    # ============================================================
    run_test(
        collection,
        test_name="Web search request — should produce TOOL",
        conv_messages=all_messages[:4],
        user_message="Can you search the web for recent news about AI consciousness research?",
        self_knowledge=self_knowledge,
        soul=soul,
    )

    # ============================================================
    # Test 5: Topic the entity has discussed before
    # ============================================================
    run_test(
        collection,
        test_name="Identity topic — should search for name/identity conversations",
        conv_messages=[],
        user_message="Do you remember how you chose your name?",
        self_knowledge=self_knowledge,
        soul=soul,
    )

    print(f"\n{'=' * 70}")
    print("PROTOTYPE COMPLETE")
    print(f"{'=' * 70}")

    conn.close()


if __name__ == "__main__":
    main()
