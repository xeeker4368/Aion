"""
Prototype v4: Hybrid Search — BM25 + ChromaDB Vector + RRF

Tests hybrid retrieval by combining:
- ChromaDB vector search (semantic similarity — what exists now)
- BM25 keyword search (term frequency — new)
- Reciprocal Rank Fusion to merge results

The BM25 index is built IN MEMORY from the existing ChromaDB chunks.
Nothing is written. Nothing is modified. Pure read-only test.

Requires: pip install rank-bm25

Run on Hades:
    pip install rank-bm25 --break-system-packages
    cd ~/aion
    ./aion/bin/python prototype_v4_hybrid.py
"""

import sqlite3
import sys
import re
from pathlib import Path
from collections import defaultdict

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

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    print("ERROR: rank-bm25 not installed.")
    print("  pip install rank-bm25 --break-system-packages")
    sys.exit(1)

# ============================================================
# Configuration
# ============================================================

OLLAMA_HOST = "http://localhost:11434"
CHAT_MODEL = "llama3.1:8b-aion"
EMBED_MODEL = "nomic-embed-text"
CONTEXT_WINDOW = 10240
RETRIEVAL_MAX_DISTANCE = 0.75
RETRIEVAL_RESULTS = 5

# RRF parameter — standard value
RRF_K = 60

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
# Build BM25 index from ChromaDB chunks (read-only)
# ============================================================

def build_bm25_index(collection):
    """
    Read ALL chunks from ChromaDB and build an in-memory BM25 index.
    Returns (bm25_index, chunk_list) where chunk_list preserves metadata.
    """
    print("Building BM25 index from ChromaDB chunks...")

    # Read everything from ChromaDB — this is a read operation
    all_data = collection.get(include=["documents", "metadatas"])

    if not all_data or not all_data["documents"]:
        print("  ERROR: No documents in ChromaDB")
        return None, []

    chunk_list = []
    tokenized_docs = []

    for i, doc in enumerate(all_data["documents"]):
        meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
        chunk_id = all_data["ids"][i] if all_data["ids"] else f"unknown_{i}"

        chunk_list.append({
            "id": chunk_id,
            "text": doc,
            "conversation_id": meta.get("conversation_id", ""),
            "source_type": meta.get("source_type", ""),
            "source_trust": meta.get("source_trust", ""),
        })

        # Tokenize for BM25 — simple whitespace + lowercasing
        tokens = _tokenize(doc)
        tokenized_docs.append(tokens)

    bm25 = BM25Okapi(tokenized_docs)
    print(f"  BM25 index built: {len(chunk_list)} chunks indexed")
    return bm25, chunk_list


def _tokenize(text):
    """Simple tokenizer for BM25 — lowercase, split on non-alpha, filter short."""
    words = re.findall(r'[a-zA-Z]+', text.lower())
    return [w for w in words if len(w) > 1]


# ============================================================
# Search functions
# ============================================================

def search_vector(collection, query, n_results=RETRIEVAL_RESULTS):
    """ChromaDB vector search (same as production)."""
    trust_weights = {"firsthand": 0.9, "secondhand": 1.0, "thirdhand": 1.1}
    fetch_count = min(n_results * 3, collection.count())

    try:
        results = collection.query(query_texts=[query], n_results=fetch_count)
    except Exception as e:
        print(f"  Vector search error: {e}")
        return []

    if not results or not results["documents"] or not results["documents"][0]:
        return []

    memories = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        raw_dist = results["distances"][0][i] if results["distances"] else None
        trust = meta.get("source_trust", "firsthand")
        weight = trust_weights.get(trust, 1.0)
        weighted = raw_dist * weight if raw_dist is not None else None
        chunk_id = results["ids"][0][i] if results["ids"] else ""
        memories.append({
            "id": chunk_id,
            "text": doc,
            "conversation_id": meta.get("conversation_id", ""),
            "distance": raw_dist,
            "weighted_distance": weighted,
            "source_type": meta.get("source_type", ""),
        })

    memories.sort(key=lambda m: m["weighted_distance"] if m["weighted_distance"] is not None else float("inf"))

    # Deduplicate by conversation, filter by threshold
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


def search_bm25(bm25, chunk_list, query, n_results=RETRIEVAL_RESULTS):
    """BM25 keyword search against the in-memory index."""
    tokens = _tokenize(query)
    if not tokens:
        return []

    scores = bm25.get_scores(tokens)

    # Pair scores with chunks, sort by score descending
    scored = list(zip(scores, chunk_list))
    scored.sort(key=lambda x: x[0], reverse=True)

    # Deduplicate by conversation, filter zero-score
    seen = set()
    results = []
    for score, chunk in scored:
        if score <= 0:
            break
        cid = chunk["conversation_id"]
        if cid not in seen:
            seen.add(cid)
            results.append({
                "id": chunk["id"],
                "text": chunk["text"],
                "conversation_id": cid,
                "bm25_score": score,
                "source_type": chunk["source_type"],
            })
        if len(results) >= n_results * 2:  # get extra for RRF
            break

    return results


def reciprocal_rank_fusion(vector_results, bm25_results, k=RRF_K,
                           n_results=RETRIEVAL_RESULTS):
    """
    Merge vector and BM25 results using Reciprocal Rank Fusion.

    RRF score = sum of 1/(k + rank) across all result lists.
    Higher is better. k=60 is standard.
    """
    rrf_scores = defaultdict(float)
    result_data = {}

    # Score vector results by rank
    for rank, r in enumerate(vector_results):
        cid = r["conversation_id"]
        rrf_scores[cid] += 1.0 / (k + rank + 1)
        if cid not in result_data:
            result_data[cid] = {
                "conversation_id": cid,
                "text": r["text"],
                "source_type": r.get("source_type", ""),
                "vector_rank": rank + 1,
                "bm25_rank": None,
                "vector_distance": r.get("weighted_distance"),
                "bm25_score": None,
            }
        else:
            result_data[cid]["vector_rank"] = rank + 1
            result_data[cid]["vector_distance"] = r.get("weighted_distance")

    # Score BM25 results by rank
    for rank, r in enumerate(bm25_results):
        cid = r["conversation_id"]
        rrf_scores[cid] += 1.0 / (k + rank + 1)
        if cid not in result_data:
            result_data[cid] = {
                "conversation_id": cid,
                "text": r["text"],
                "source_type": r.get("source_type", ""),
                "vector_rank": None,
                "bm25_rank": rank + 1,
                "vector_distance": None,
                "bm25_score": r.get("bm25_score"),
            }
        else:
            result_data[cid]["bm25_rank"] = rank + 1
            result_data[cid]["bm25_score"] = r.get("bm25_score")

    # Sort by RRF score descending
    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

    results = []
    for cid, score in ranked[:n_results]:
        data = result_data[cid]
        data["rrf_score"] = score
        results.append(data)

    return results


# ============================================================
# LLM reasoning step
# ============================================================

REASONING_INSTRUCTION = """You are about to respond to a new message. First, decide what you need.

MEMORIES: If this message relates to something from your past, you need to search your memory. Think about the specific words and phrases that were actually said in those past conversations. Write those words as your search terms.

TOOLS: If this message asks you to look something up on the web, say exactly: TOOL: web_search
If it asks you to read a URL, say exactly: TOOL: web_fetch

SKIP: If this is just a greeting, a thank you, or small talk, say exactly: SKIP

Respond with ONLY your search words, or TOOL: web_search, or TOOL: web_fetch, or SKIP. Nothing else."""


def build_reasoning_context(conversation_messages, self_knowledge, soul):
    parts = []
    if conversation_messages:
        history_lines = []
        for msg in conversation_messages:
            ts = format_timestamp(msg["timestamp"])
            history_lines.append(f"[{ts}] {msg['role']}: {msg['content']}")
        parts.append("Here is the conversation you are currently having:\n\n"
                      + "\n".join(history_lines))
    if self_knowledge:
        parts.append(f"\nWhat you have learned about yourself through experience:\n\n"
                      f"{self_knowledge}")
    parts.append(f"\n{soul}")
    return "\n".join(parts)


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

def run_test(collection, bm25, chunk_list, test_name, conv_messages,
             user_message, self_knowledge, soul):
    print(f"\n{'=' * 70}")
    print(f"TEST: {test_name}")
    print(f"User: \"{user_message[:100]}\"")
    print(f"{'=' * 70}")

    # --- Reasoning step ---
    system_ctx = build_reasoning_context(conv_messages, self_knowledge, soul)
    user_content = f"New message from the user: \"{user_message}\"\n\n{REASONING_INSTRUCTION}"
    reasoning_output = call_reasoning(system_ctx, user_content)
    print(f"\n  Reasoning: \"{reasoning_output}\"")

    lower = reasoning_output.lower().strip()
    if lower in ("skip", "skip."):
        print(f"  Decision: SKIP")
        return
    if lower.startswith("tool:"):
        print(f"  Decision: {reasoning_output}")
        return

    # Use the raw user message for search (the reasoning output guides
    # what we search for, but let's test both)
    search_query = user_message

    # --- Vector search (current system) ---
    print(f"\n--- VECTOR ONLY (current system) ---")
    vector_results = search_vector(collection, search_query, n_results=10)
    has_relay_vector = False
    for i, r in enumerate(vector_results[:5]):
        marker = ""
        if r["conversation_id"].startswith(RELAY_CONV_PREFIX):
            marker = " *** RELAY ***"
            has_relay_vector = True
        preview = r["text"][:70].replace("\n", " ")
        print(f"  {i+1}. [{r['weighted_distance']:.4f}] "
              f"{r['source_type']}/{r['conversation_id'][:8]}... {preview}{marker}")

    # --- BM25 search ---
    print(f"\n--- BM25 ONLY ---")
    bm25_results = search_bm25(bm25, chunk_list, search_query, n_results=10)
    has_relay_bm25 = False
    for i, r in enumerate(bm25_results[:5]):
        marker = ""
        if r["conversation_id"].startswith(RELAY_CONV_PREFIX):
            marker = " *** RELAY ***"
            has_relay_bm25 = True
        preview = r["text"][:70].replace("\n", " ")
        print(f"  {i+1}. [score: {r['bm25_score']:.4f}] "
              f"{r['source_type']}/{r['conversation_id'][:8]}... {preview}{marker}")

    # --- Hybrid: RRF merge ---
    print(f"\n--- HYBRID (Vector + BM25 merged with RRF) ---")
    hybrid_results = reciprocal_rank_fusion(vector_results, bm25_results)
    has_relay_hybrid = False
    for i, r in enumerate(hybrid_results):
        marker = ""
        if r["conversation_id"].startswith(RELAY_CONV_PREFIX):
            marker = " *** RELAY ***"
            has_relay_hybrid = True
        v_rank = f"V:{r['vector_rank']}" if r['vector_rank'] else "V:—"
        b_rank = f"B:{r['bm25_rank']}" if r['bm25_rank'] else "B:—"
        preview = r["text"][:70].replace("\n", " ")
        print(f"  {i+1}. [RRF: {r['rrf_score']:.6f}] [{v_rank} {b_rank}] "
              f"{r['source_type']}/{r['conversation_id'][:8]}... {preview}{marker}")

    # --- Verdict ---
    print(f"\n--- VERDICT ---")
    print(f"  Vector only:   {'FOUND' if has_relay_vector else 'MISSED'}")
    print(f"  BM25 only:     {'FOUND' if has_relay_bm25 else 'MISSED'}")
    print(f"  Hybrid (RRF):  {'FOUND' if has_relay_hybrid else 'MISSED'}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v4: Hybrid Search — BM25 + Vector + RRF")
    print("=" * 70)

    conn = connect_db()
    collection = connect_chroma()
    print(f"ChromaDB: {collection.count()} chunks")

    # Build BM25 index from the same chunks (read-only)
    bm25, chunk_list = build_bm25_index(collection)
    if bm25 is None:
        sys.exit(1)

    soul = load_soul()
    self_knowledge = load_self_knowledge(conn)
    print(f"SOUL.md: ~{len(soul)//4} tokens")
    print(f"Self-knowledge: ~{len(self_knowledge)//4} tokens")

    full_id, all_messages = load_conversation(conn, TARGET_CONV_PREFIX)
    if not all_messages:
        sys.exit(1)
    print(f"Target conv: {full_id[:8]}... ({len(all_messages)} messages)")

    # ============================================================
    # Test 1: "most important difference between you two"
    # ============================================================
    run_test(
        collection, bm25, chunk_list,
        test_name='Claude difference — "most important difference"',
        conv_messages=all_messages[:4],
        user_message=all_messages[4]["content"],
        self_knowledge=self_knowledge,
        soul=soul,
    )

    # ============================================================
    # Test 2: "summarize everything claude told you yesterday"
    # ============================================================
    run_test(
        collection, bm25, chunk_list,
        test_name='Summarize Claude conversation',
        conv_messages=all_messages[:12],
        user_message=all_messages[12]["content"],
        self_knowledge=self_knowledge,
        soul=soul,
    )

    # ============================================================
    # Test 3: Greeting
    # ============================================================
    run_test(
        collection, bm25, chunk_list,
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
        collection, bm25, chunk_list,
        test_name="Web search (should TOOL)",
        conv_messages=all_messages[:4],
        user_message="Can you search the web for recent news about AI consciousness research?",
        self_knowledge=self_knowledge,
        soul=soul,
    )

    # ============================================================
    # Test 5: Name choice
    # ============================================================
    run_test(
        collection, bm25, chunk_list,
        test_name="Name choice",
        conv_messages=[],
        user_message="Do you remember how you chose your name?",
        self_knowledge=self_knowledge,
        soul=soul,
    )

    print(f"\n{'=' * 70}")
    print("PROTOTYPE v4 COMPLETE")
    print(f"{'=' * 70}")
    conn.close()


if __name__ == "__main__":
    main()
