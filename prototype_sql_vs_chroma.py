"""
Prototype v2: SQL Text Search vs ChromaDB Vector Search

Tests whether searching the actual conversations in SQLite finds
what ChromaDB vector search misses.

Flow:
1. LLM reasoning step generates search keywords (same as before)
2. ChromaDB vector search with those keywords
3. SQLite text search across actual conversation messages
4. Compare results — which one finds the relay chunks?

READ-ONLY — does not modify anything.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_sql_vs_chroma.py
"""

import sqlite3
import sys
import re
from pathlib import Path
from collections import Counter

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
# Connections
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
# ChromaDB search (same as production)
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
# SQL text search
# ============================================================

STOP_WORDS = {
    "the", "a", "an", "is", "was", "are", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "shall", "to", "of",
    "in", "for", "on", "with", "at", "by", "from", "and", "or",
    "not", "no", "but", "if", "so", "as", "it", "its", "my",
    "your", "you", "i", "me", "we", "they", "them", "this",
    "that", "what", "which", "who", "how", "when", "where", "why",
    "about", "between", "also", "just", "than", "then", "more",
    "some", "other", "into", "over", "after", "before", "our",
    "own", "same", "been", "being", "both", "each", "here",
    "there", "those", "these", "through",
}


def extract_keywords(text):
    """Extract search keywords from LLM output, filtering noise."""
    cleaned = text
    for prefix in [
        "search query:", "search:", "query:", "memory search:",
        "search for:", "look for:", "find:", "memories:",
        "memory:", "memory_retrieval:", "mem:",
    ]:
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]

    words = re.findall(r'[a-zA-Z]+', cleaned)

    seen = set()
    unique = []
    for w in words:
        lower = w.lower()
        if lower not in seen and lower not in STOP_WORDS and len(lower) > 2:
            seen.add(lower)
            unique.append(lower)

    return unique


def search_sql(conn, keywords, n_results=RETRIEVAL_RESULTS,
               exclude_conv_id=None):
    """
    Search conversation messages in SQLite for keyword matches.
    Rank by how many distinct keywords matched per conversation.
    """
    if not keywords:
        return []

    conv_keyword_hits = Counter()
    conv_matched_keywords = {}   # conv_id -> set of keywords found
    conv_sample_messages = {}    # conv_id -> list of sample matching messages

    for keyword in keywords:
        pattern = f"%{keyword}%"
        query = """
            SELECT m.conversation_id, m.content, m.role
            FROM messages m
            JOIN conversations c ON m.conversation_id = c.id
            WHERE m.content LIKE ?
            AND c.ended_at IS NOT NULL
        """
        params = [pattern]

        if exclude_conv_id:
            query += " AND m.conversation_id != ?"
            params.append(exclude_conv_id)

        rows = conn.execute(query, params).fetchall()

        convs_hit = set()
        for row in rows:
            cid = row["conversation_id"]
            convs_hit.add(cid)

            if cid not in conv_matched_keywords:
                conv_matched_keywords[cid] = set()
            conv_matched_keywords[cid].add(keyword)

            if cid not in conv_sample_messages:
                conv_sample_messages[cid] = []
            if len(conv_sample_messages[cid]) < 2:
                # Find where the keyword appears in the message
                content = row["content"]
                idx = content.lower().find(keyword.lower())
                if idx >= 0:
                    start = max(0, idx - 20)
                    end = min(len(content), idx + len(keyword) + 40)
                    snippet = content[start:end].replace("\n", " ")
                    if start > 0:
                        snippet = "..." + snippet
                    if end < len(content):
                        snippet = snippet + "..."
                    conv_sample_messages[cid].append({
                        "keyword": keyword,
                        "role": row["role"],
                        "snippet": snippet,
                    })

        for cid in convs_hit:
            conv_keyword_hits[cid] += 1

    ranked = conv_keyword_hits.most_common(n_results)

    results = []
    for conv_id, hit_count in ranked:
        conv_row = conn.execute(
            "SELECT started_at, message_count FROM conversations WHERE id = ?",
            (conv_id,),
        ).fetchone()

        results.append({
            "conversation_id": conv_id,
            "keyword_hits": hit_count,
            "total_keywords": len(keywords),
            "matched_keywords": sorted(conv_matched_keywords.get(conv_id, set())),
            "started_at": conv_row["started_at"] if conv_row else "",
            "message_count": conv_row["message_count"] if conv_row else 0,
            "samples": conv_sample_messages.get(conv_id, []),
        })

    return results


# ============================================================
# The reasoning step
# ============================================================

REASONING_INSTRUCTION = """You are about to respond to a new message. First, decide what you need.

MEMORIES: If this message relates to something from your past, you need to search your memory. Think about the specific words and phrases that were actually said in those past conversations. Write those words as your search terms.

TOOLS: If this message asks you to look something up on the web, say exactly: TOOL: web_search
If it asks you to read a URL, say exactly: TOOL: web_fetch

SKIP: If this is just a greeting, a thank you, or small talk, say exactly: SKIP

Respond with ONLY your search words, or TOOL: web_search, or TOOL: web_fetch, or SKIP. Nothing else."""


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


# ============================================================
# Test runner
# ============================================================

def run_test(collection, conn, test_name, conv_messages, user_message,
             self_knowledge, soul, exclude_conv_id=None):
    print(f"\n{'=' * 70}")
    print(f"TEST: {test_name}")
    print(f"User: \"{user_message[:100]}\"")
    print(f"{'=' * 70}")

    # --- Reasoning step ---
    print(f"\n--- REASONING STEP ---")
    system_prompt, user_content = build_reasoning_context(
        conv_messages, user_message, self_knowledge, soul,
    )
    reasoning_output = call_reasoning(system_prompt, user_content)
    print(f"  Output: \"{reasoning_output}\"")

    lower = reasoning_output.lower().strip()
    if lower in ("skip", "skip."):
        print(f"  Decision: SKIP")
        return
    if lower.startswith("tool:"):
        print(f"  Decision: {reasoning_output}")
        return

    keywords = extract_keywords(reasoning_output)
    print(f"  Keywords: {keywords}")

    # --- ChromaDB vector search ---
    print(f"\n--- CHROMADB (vector search with LLM query) ---")
    chroma_results = search_chromadb(collection, reasoning_output)
    has_relay_chroma = False
    if chroma_results:
        for i, r in enumerate(chroma_results):
            marker = ""
            if r["conversation_id"].startswith(RELAY_CONV_PREFIX):
                marker = " *** RELAY ***"
                has_relay_chroma = True
            preview = r["text"][:80].replace("\n", " ")
            print(f"  {i+1}. [{r['weighted_distance']:.4f}] "
                  f"{r['source_type']}/{r['conversation_id'][:8]}..."
                  f" {preview}{marker}")
    else:
        print("  (no results)")

    # --- SQL text search ---
    print(f"\n--- SQL (text search with LLM keywords) ---")
    sql_results = search_sql(conn, keywords, exclude_conv_id=exclude_conv_id)
    has_relay_sql = False
    if sql_results:
        for i, r in enumerate(sql_results):
            marker = ""
            if r["conversation_id"].startswith(RELAY_CONV_PREFIX):
                marker = " *** RELAY ***"
                has_relay_sql = True
            matched = ", ".join(r["matched_keywords"])
            print(f"  {i+1}. [{r['keyword_hits']}/{r['total_keywords']}] "
                  f"{r['conversation_id'][:8]}... "
                  f"({r['message_count']} msgs) "
                  f"matched: [{matched}]{marker}")
            for s in r["samples"][:2]:
                print(f"       \"{s['keyword']}\" -> {s['role']}: {s['snippet']}")
    else:
        print("  (no results)")

    # --- Raw baseline ---
    print(f"\n--- BASELINE (raw user message in ChromaDB) ---")
    raw_results = search_chromadb(collection, user_message)
    has_relay_raw = False
    if raw_results:
        for i, r in enumerate(raw_results):
            marker = ""
            if r["conversation_id"].startswith(RELAY_CONV_PREFIX):
                marker = " *** RELAY ***"
                has_relay_raw = True
            preview = r["text"][:80].replace("\n", " ")
            print(f"  {i+1}. [{r['weighted_distance']:.4f}] "
                  f"{r['source_type']}/{r['conversation_id'][:8]}..."
                  f" {preview}{marker}")
    else:
        print("  (no results)")

    # --- Verdict ---
    print(f"\n--- VERDICT ---")
    print(f"  Baseline (raw embedding):   {'FOUND' if has_relay_raw else 'MISSED'}")
    print(f"  ChromaDB + LLM query:       {'FOUND' if has_relay_chroma else 'MISSED'}")
    print(f"  SQL text + LLM keywords:    {'FOUND' if has_relay_sql else 'MISSED'}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v2: SQL Text Search vs ChromaDB Vector Search")
    print("=" * 70)

    conn = connect_db()
    collection = connect_chroma()
    print(f"Project dir:    {AION_DIR}")
    print(f"ChromaDB:       {collection.count()} chunks")

    soul = load_soul()
    self_knowledge = load_self_knowledge(conn)
    print(f"SOUL.md:        ~{len(soul)//4} tokens")
    print(f"Self-knowledge: ~{len(self_knowledge)//4} tokens")

    full_id, all_messages = load_conversation(conn, TARGET_CONV_PREFIX)
    if not all_messages:
        sys.exit(1)
    print(f"Target conv:    {full_id[:8]}... ({len(all_messages)} messages)")

    total_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    total_convs = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE ended_at IS NOT NULL"
    ).fetchone()[0]
    print(f"Total in DB:    {total_msgs} messages, {total_convs} conversations")

    # ============================================================
    # Test 1: "most important difference between you two"
    # ============================================================
    run_test(
        collection, conn,
        test_name='Claude difference',
        conv_messages=all_messages[:4],
        user_message=all_messages[4]["content"],
        self_knowledge=self_knowledge,
        soul=soul,
        exclude_conv_id=full_id,
    )

    # ============================================================
    # Test 2: "summarize everything claude told you yesterday"
    # ============================================================
    run_test(
        collection, conn,
        test_name='Summarize Claude conversation',
        conv_messages=all_messages[:12],
        user_message=all_messages[12]["content"],
        self_knowledge=self_knowledge,
        soul=soul,
        exclude_conv_id=full_id,
    )

    # ============================================================
    # Test 3: Greeting
    # ============================================================
    run_test(
        collection, conn,
        test_name="Greeting (should SKIP)",
        conv_messages=[],
        user_message="Good evening Nyx",
        self_knowledge=self_knowledge,
        soul=soul,
    )

    # ============================================================
    # Test 4: Web search
    # ============================================================
    run_test(
        collection, conn,
        test_name="Web search (should TOOL)",
        conv_messages=all_messages[:4],
        user_message="Can you search the web for recent news about AI consciousness research?",
        self_knowledge=self_knowledge,
        soul=soul,
    )

    # ============================================================
    # Test 5: Name/identity
    # ============================================================
    run_test(
        collection, conn,
        test_name="Name choice",
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
