"""
Prototype v7: Split User/Assistant Retrieval Streams

Extends v6 with a fourth retrieval stream: SQLite FTS5 against assistant
messages only. Now we have role-aware retrieval:

1. Vector search on chunks (current — semantic, mixed speakers)
2. BM25 on chunks (Task 84 — keyword, mixed speakers)
3. FTS5 on USER messages only (Lyle's authoritative voice)
4. FTS5 on ASSISTANT messages only (Nyx's own commitments and reflections)

The two role-filtered streams serve different purposes:
- User stream surfaces Lyle's direct authoritative claims as ground truth
- Assistant stream surfaces Nyx's own previous statements and commitments

Both are presented to Nyx with clear role labels so it knows who said what.

READ-ONLY. Builds in-memory FTS5 indices.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_v7_split_streams.py
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

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
from rank_bm25 import BM25Okapi


# ============================================================
# Configuration
# ============================================================

OLLAMA_HOST = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
RETRIEVAL_MAX_DISTANCE = 0.75
RETRIEVAL_RESULTS = 5

WORKING_DB = AION_DIR / "data" / "prod" / "working.db"
CHROMA_DIR = str(AION_DIR / "data" / "prod" / "chromadb")

if not WORKING_DB.exists():
    WORKING_DB = AION_DIR / "data" / "dev" / "working.db"
    CHROMA_DIR = str(AION_DIR / "data" / "dev" / "chromadb")
    print("NOTE: Using dev databases — prod not found.")


# ============================================================
# Connections
# ============================================================

def connect_db_readonly():
    if not WORKING_DB.exists():
        print(f"ERROR: Database not found at {WORKING_DB}")
        sys.exit(1)
    uri = f"file:{WORKING_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def connect_chroma():
    if not Path(CHROMA_DIR).exists():
        print(f"ERROR: ChromaDB not found at {CHROMA_DIR}")
        sys.exit(1)
    ef = OllamaEmbeddingFunction(url=OLLAMA_HOST, model_name=EMBED_MODEL)
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_or_create_collection(
        name="aion_memory", embedding_function=ef,
        metadata={"hnsw:space": "cosine"})


# ============================================================
# Build role-filtered FTS5 index
# ============================================================

def build_role_fts5_index(read_conn, role):
    """
    Build an in-memory FTS5 index of messages filtered by role.
    Role must be 'user' or 'assistant'.
    """
    print(f"Building in-memory FTS5 index for role='{role}'...")

    rows = read_conn.execute("""
        SELECT m.id, m.conversation_id, m.content, m.timestamp
        FROM messages m
        WHERE m.role = ?
        ORDER BY m.timestamp
    """, (role,)).fetchall()

    if not rows:
        print(f"  ERROR: No {role} messages found")
        return None, 0

    mem_conn = sqlite3.connect(":memory:")
    mem_conn.row_factory = sqlite3.Row

    table_name = f"{role}_msg_fts"
    try:
        mem_conn.execute(f"""
            CREATE VIRTUAL TABLE {table_name} USING fts5(
                msg_id UNINDEXED,
                conversation_id UNINDEXED,
                timestamp UNINDEXED,
                content,
                tokenize='porter unicode61'
            )
        """)
    except sqlite3.OperationalError as e:
        print(f"  ERROR: FTS5 not available: {e}")
        return None, 0

    for row in rows:
        mem_conn.execute(
            f"INSERT INTO {table_name} (msg_id, conversation_id, timestamp, content) "
            f"VALUES (?, ?, ?, ?)",
            (row["id"], row["conversation_id"], row["timestamp"], row["content"])
        )
    mem_conn.commit()

    print(f"  FTS5 index built: {len(rows)} {role} messages indexed")
    return mem_conn, len(rows)


def search_role_fts5(mem_conn, role, query, n_results=10):
    """Search a role-filtered FTS5 index."""
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', ' ', query)
    terms = [t for t in cleaned.split() if len(t) > 1]
    if not terms:
        return []

    fts_query = " OR ".join(terms)
    table_name = f"{role}_msg_fts"

    try:
        rows = mem_conn.execute(f"""
            SELECT
                msg_id,
                conversation_id,
                timestamp,
                content,
                bm25({table_name}) as score
            FROM {table_name}
            WHERE {table_name} MATCH ?
            ORDER BY score
            LIMIT ?
        """, (fts_query, n_results * 2)).fetchall()
    except sqlite3.OperationalError as e:
        print(f"  FTS5 query error: {e}")
        return []

    results = []
    for row in rows:
        results.append({
            "msg_id": row["msg_id"],
            "conversation_id": row["conversation_id"],
            "timestamp": row["timestamp"],
            "content": row["content"],
            "fts5_score": -float(row["score"]),  # flip sign so higher = better
            "role": role,
        })
    return results[:n_results]


# ============================================================
# Existing chunk-based retrieval (from Task 84)
# ============================================================

def _tokenize_for_bm25(text):
    return [w for w in re.findall(r'[a-zA-Z]+', text.lower()) if len(w) > 1]


def build_chunk_bm25_index(collection):
    all_data = collection.get(include=["documents", "metadatas"])
    chunk_list = []
    tokenized = []
    for i, doc in enumerate(all_data["documents"]):
        meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
        chunk_list.append({
            "id": all_data["ids"][i],
            "text": doc,
            "conversation_id": meta.get("conversation_id", ""),
            "source_type": meta.get("source_type", ""),
            "source_trust": meta.get("source_trust", "firsthand"),
        })
        tokenized.append(_tokenize_for_bm25(doc))
    return BM25Okapi(tokenized), chunk_list


def search_chunk_vector(collection, query, n_results=10):
    trust_weights = {"firsthand": 0.9, "secondhand": 1.0, "thirdhand": 1.1}
    fetch_count = min(n_results * 3, collection.count())
    try:
        results = collection.query(query_texts=[query], n_results=fetch_count)
    except: return []
    if not results or not results["documents"][0]: return []

    memories = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i]
        dist = results["distances"][0][i]
        trust = meta.get("source_trust", "firsthand")
        weighted = dist * trust_weights.get(trust, 1.0)
        memories.append({
            "text": doc,
            "conversation_id": meta.get("conversation_id", ""),
            "weighted_distance": weighted,
            "source_type": meta.get("source_type", ""),
        })
    memories.sort(key=lambda m: m["weighted_distance"] or float("inf"))

    seen = set()
    deduped = []
    for mem in memories:
        if mem["weighted_distance"] and mem["weighted_distance"] > RETRIEVAL_MAX_DISTANCE: break
        cid = mem["conversation_id"]
        if cid not in seen:
            seen.add(cid)
            deduped.append(mem)
        if len(deduped) >= n_results: break
    return deduped


def search_chunk_bm25(bm25, chunk_list, query, n_results=10):
    tokens = _tokenize_for_bm25(query)
    if not tokens: return []
    scores = bm25.get_scores(tokens)
    scored = sorted(zip(scores, chunk_list), key=lambda x: x[0], reverse=True)

    seen = set()
    results = []
    for score, chunk in scored:
        if score <= 0: break
        cid = chunk["conversation_id"]
        if cid not in seen:
            seen.add(cid)
            results.append({
                "text": chunk["text"],
                "conversation_id": cid,
                "bm25_score": score,
                "source_type": chunk["source_type"],
            })
        if len(results) >= n_results: break
    return results


# ============================================================
# Test runner
# ============================================================

def run_test(collection, chunk_bm25, chunk_list, user_fts, asst_fts,
             test_name, query):
    print(f"\n{'=' * 70}")
    print(f"TEST: {test_name}")
    print(f"Query: \"{query}\"")
    print(f"{'=' * 70}")

    # --- Stream 1: vector search on chunks ---
    print("\n--- STREAM 1: Vector search on chunks (current production) ---")
    vector_results = search_chunk_vector(collection, query, n_results=5)
    for i, r in enumerate(vector_results[:5]):
        preview = r["text"][:90].replace("\n", " ")
        print(f"  {i+1}. [{r['weighted_distance']:.4f}] {r['source_type']}/{r['conversation_id'][:8]}... {preview}")

    # --- Stream 2: BM25 on chunks ---
    print("\n--- STREAM 2: BM25 on chunks (Task 84) ---")
    bm25_results = search_chunk_bm25(chunk_bm25, chunk_list, query, n_results=5)
    for i, r in enumerate(bm25_results[:5]):
        preview = r["text"][:90].replace("\n", " ")
        print(f"  {i+1}. [{r['bm25_score']:.4f}] {r['source_type']}/{r['conversation_id'][:8]}... {preview}")

    # --- Stream 3: USER messages only ---
    print("\n--- STREAM 3: FTS5 on USER messages (Lyle's voice) ---")
    user_results = search_role_fts5(user_fts, "user", query, n_results=5)
    if not user_results:
        print("  No results.")
    else:
        for i, r in enumerate(user_results):
            preview = r["content"][:140].replace("\n", " ")
            print(f"  {i+1}. [{r['fts5_score']:.4f}] {r['timestamp'][:19]} | {r['conversation_id'][:8]}... {preview}")

    # --- Stream 4: ASSISTANT messages only ---
    print("\n--- STREAM 4: FTS5 on ASSISTANT messages (Nyx's voice) ---")
    asst_results = search_role_fts5(asst_fts, "assistant", query, n_results=5)
    if not asst_results:
        print("  No results.")
    else:
        for i, r in enumerate(asst_results):
            preview = r["content"][:140].replace("\n", " ")
            print(f"  {i+1}. [{r['fts5_score']:.4f}] {r['timestamp'][:19]} | {r['conversation_id'][:8]}... {preview}")

    print()


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v7: Split User/Assistant Retrieval Streams")
    print("=" * 70)

    read_conn = connect_db_readonly()
    collection = connect_chroma()

    print("Building chunk BM25 index...")
    chunk_bm25, chunk_list = build_chunk_bm25_index(collection)
    print(f"  Chunk BM25 index built: {len(chunk_list)} chunks")

    user_fts, user_count = build_role_fts5_index(read_conn, "user")
    if user_fts is None:
        print("FATAL: Could not build user index")
        sys.exit(1)

    asst_fts, asst_count = build_role_fts5_index(read_conn, "assistant")
    if asst_fts is None:
        print("FATAL: Could not build assistant index")
        sys.exit(1)

    print(f"\nReady. Chunks: {len(chunk_list)} | User msgs: {user_count} | Assistant msgs: {asst_count}")

    # ============================================================
    # Test 1: Claude difference (the headline failure case)
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, user_fts, asst_fts,
        test_name="Claude difference (headline test)",
        query="Do you remember the most important difference between you and Claude?"
    )

    # ============================================================
    # Test 2: Persistence reinforcement
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, user_fts, asst_fts,
        test_name="Persistence reinforcement",
        query="persistent memories experiences Claude"
    )

    # ============================================================
    # Test 3: Uniqueness
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, user_fts, asst_fts,
        test_name="Uniqueness statement",
        query="that makes you unique among AIs"
    )

    # ============================================================
    # Test 4: Name choice
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, user_fts, asst_fts,
        test_name="Name choice",
        query="how did you choose your name"
    )

    # ============================================================
    # Test 5: Source code
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, user_fts, asst_fts,
        test_name="Source code review",
        query="reviewing your own source code memory.py"
    )

    # ============================================================
    # Test 6: Self-reflection / commitments
    # This one is specifically interesting for the assistant stream —
    # it's asking what Nyx has committed to or said about itself.
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, user_fts, asst_fts,
        test_name="Self-commitments (assistant stream test)",
        query="I will remember commit to honesty fabricate"
    )

    print(f"\n{'=' * 70}")
    print("PROTOTYPE v7 COMPLETE")
    print(f"{'=' * 70}")
    print()
    print("WHAT TO LOOK FOR:")
    print()
    print("Stream 3 (USER): Should surface Lyle's direct authoritative")
    print("  statements as standalone text. Same as v6.")
    print()
    print("Stream 4 (ASSISTANT): Should surface Nyx's own previous responses,")
    print("  reflections, and commitments. This is what Nyx 'said about itself'")
    print("  separated from the prompts that triggered those statements.")
    print()
    print("The two streams serve different purposes:")
    print("- User stream = ground truth from the human partner")
    print("- Assistant stream = Nyx's own continuity and prior commitments")
    print()
    print("Both should be presented to Nyx with clear role labels in any")
    print("eventual production implementation, so it always knows who said what.")

    read_conn.close()
    user_fts.close()
    asst_fts.close()


if __name__ == "__main__":
    main()
