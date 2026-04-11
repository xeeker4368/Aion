"""
Prototype v6: User Voice Retrieval Stream

The Session 19 finding: ChromaDB chunks contain both speakers interleaved,
and Nyx's text outweighs Lyle's text ~9-to-1 within the same chunks. The
result is that retrieval pulls in chunks where Nyx's hedged responses
dominate the in-context learning signal, and Nyx mirrors the hedge.
Lyle's authoritative reinforcements get drowned out inside the same chunks.

This prototype tests a third retrieval stream: SQLite FTS5 against the
messages table, filtered by role='user'. This pulls Lyle's direct
statements as standalone text, with no Nyx response sandwiched next to
them inside a chunk.

Architecture under test:
1. Vector search on chunks (current — semantic similarity, mixed speakers)
2. BM25 on chunks (Task 84 — keyword matching, mixed speakers)
3. SQL FTS5 on user messages (NEW — keyword matching, Lyle voice only)
4. Three-way RRF merge

If stream 3 reliably surfaces Lyle's reinforcement statements for the
Claude question, that's strong evidence to add it to memory.py as Task 86.

READ-ONLY. Builds an in-memory FTS5 index from the messages table.
Production data is not modified.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_v6_user_voice.py
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
RRF_K = 60

# Use the PROD database for this test (read-only) so we're testing
# against the actual data the production system would see.
WORKING_DB = AION_DIR / "data" / "prod" / "working.db"
CHROMA_DIR = str(AION_DIR / "data" / "prod" / "chromadb")

# Fall back to dev if prod doesn't exist
if not WORKING_DB.exists():
    WORKING_DB = AION_DIR / "data" / "dev" / "working.db"
    CHROMA_DIR = str(AION_DIR / "data" / "dev" / "chromadb")
    print("NOTE: Using dev databases — prod not found.")


# ============================================================
# Connections
# ============================================================

def connect_db_readonly():
    """Open the working database in read-only mode."""
    if not WORKING_DB.exists():
        print(f"ERROR: Database not found at {WORKING_DB}")
        sys.exit(1)
    # SQLite read-only URI
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
# Build the user-only FTS5 index in memory
# ============================================================

def build_user_fts5_index(read_conn):
    """
    Read all user-role messages from the working database, build a
    fresh in-memory SQLite FTS5 virtual table indexed on the content.

    Returns a connection to the in-memory database with the FTS5
    table populated and ready to query.
    """
    print("Building in-memory FTS5 index from user messages...")

    # Pull all user messages from the read-only working DB
    rows = read_conn.execute("""
        SELECT m.id, m.conversation_id, m.content, m.timestamp
        FROM messages m
        WHERE m.role = 'user'
        ORDER BY m.timestamp
    """).fetchall()

    if not rows:
        print("  ERROR: No user messages found in working.db")
        return None, 0

    # Create a fresh in-memory database with FTS5
    mem_conn = sqlite3.connect(":memory:")
    mem_conn.row_factory = sqlite3.Row

    # Verify FTS5 is available
    try:
        mem_conn.execute("""
            CREATE VIRTUAL TABLE user_msg_fts USING fts5(
                msg_id UNINDEXED,
                conversation_id UNINDEXED,
                timestamp UNINDEXED,
                content,
                tokenize='porter unicode61'
            )
        """)
    except sqlite3.OperationalError as e:
        print(f"  ERROR: FTS5 not available: {e}")
        print("  This SQLite build does not support FTS5. Cannot proceed.")
        return None, 0

    # Populate
    for row in rows:
        mem_conn.execute(
            "INSERT INTO user_msg_fts (msg_id, conversation_id, timestamp, content) "
            "VALUES (?, ?, ?, ?)",
            (row["id"], row["conversation_id"], row["timestamp"], row["content"])
        )
    mem_conn.commit()

    print(f"  FTS5 index built: {len(rows)} user messages indexed")
    return mem_conn, len(rows)


def search_user_fts5(mem_conn, query, n_results=10):
    """
    Search the in-memory user-only FTS5 index using BM25 ranking.
    FTS5 has BM25 scoring built in via the bm25() auxiliary function.
    """
    # FTS5 needs query terms to be alphanumeric. Strip punctuation.
    # Use OR between terms for broader matching.
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', ' ', query)
    terms = [t for t in cleaned.split() if len(t) > 1]
    if not terms:
        return []

    # Build OR query for FTS5
    fts_query = " OR ".join(terms)

    try:
        rows = mem_conn.execute("""
            SELECT
                msg_id,
                conversation_id,
                timestamp,
                content,
                bm25(user_msg_fts) as score
            FROM user_msg_fts
            WHERE user_msg_fts MATCH ?
            ORDER BY score
            LIMIT ?
        """, (fts_query, n_results * 2)).fetchall()
    except sqlite3.OperationalError as e:
        print(f"  FTS5 query error: {e}")
        return []

    # FTS5 bm25() returns NEGATIVE scores (more negative = better match)
    # Convert to positive for consistency with our other scoring
    results = []
    for row in rows:
        results.append({
            "msg_id": row["msg_id"],
            "conversation_id": row["conversation_id"],
            "timestamp": row["timestamp"],
            "content": row["content"],
            "fts5_score": -float(row["score"]),  # flip sign
            "source_type": "user_message",
        })

    return results[:n_results]


# ============================================================
# Existing hybrid search components (from Task 84)
# ============================================================

def _tokenize_for_bm25(text):
    return [w for w in re.findall(r'[a-zA-Z]+', text.lower()) if len(w) > 1]


def build_chunk_bm25_index(collection):
    """Build the same in-memory BM25 index that memory.py uses now."""
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
# Three-way RRF merge
# ============================================================

def three_way_rrf(vector_results, bm25_results, fts5_results, k=RRF_K, n_results=RETRIEVAL_RESULTS):
    """
    Merge three retrieval streams using Reciprocal Rank Fusion.

    Streams 1 & 2 (vector, chunk-BM25) are deduplicated by conversation_id
    because they return chunks. Stream 3 (FTS5) returns individual user
    messages, so each result is identified by msg_id.

    For the merge, we treat each result as a (source_id, content) pair
    where source_id is conversation_id for chunks and msg_id for messages.
    """
    rrf_scores = defaultdict(float)
    result_data = {}

    # Stream 1: vector chunks
    for rank, r in enumerate(vector_results):
        sid = f"chunk:{r['conversation_id']}"
        rrf_scores[sid] += 1.0 / (k + rank + 1)
        if sid not in result_data:
            result_data[sid] = {
                "source_id": sid,
                "stream": "chunk",
                "text": r["text"],
                "conversation_id": r["conversation_id"],
                "vector_rank": rank + 1,
                "bm25_rank": None,
                "fts5_rank": None,
            }

    # Stream 2: chunk BM25
    for rank, r in enumerate(bm25_results):
        sid = f"chunk:{r['conversation_id']}"
        rrf_scores[sid] += 1.0 / (k + rank + 1)
        if sid not in result_data:
            result_data[sid] = {
                "source_id": sid,
                "stream": "chunk",
                "text": r["text"],
                "conversation_id": r["conversation_id"],
                "vector_rank": None,
                "bm25_rank": rank + 1,
                "fts5_rank": None,
            }
        else:
            result_data[sid]["bm25_rank"] = rank + 1

    # Stream 3: FTS5 user messages — these are SEPARATE entries, not chunks
    for rank, r in enumerate(fts5_results):
        sid = f"user_msg:{r['msg_id']}"
        rrf_scores[sid] += 1.0 / (k + rank + 1)
        result_data[sid] = {
            "source_id": sid,
            "stream": "user_message",
            "text": r["content"],
            "conversation_id": r["conversation_id"],
            "timestamp": r["timestamp"],
            "vector_rank": None,
            "bm25_rank": None,
            "fts5_rank": rank + 1,
            "fts5_score": r["fts5_score"],
        }

    # Sort by RRF score descending
    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    merged = []
    for sid, score in ranked[:n_results]:
        data = result_data[sid]
        data["rrf_score"] = score
        merged.append(data)
    return merged


# ============================================================
# Test runner
# ============================================================

def run_test(collection, chunk_bm25, chunk_list, fts5_conn, test_name, query):
    print(f"\n{'=' * 70}")
    print(f"TEST: {test_name}")
    print(f"Query: \"{query}\"")
    print(f"{'=' * 70}")

    # --- Stream 1: vector search on chunks ---
    print("\n--- STREAM 1: Vector search on chunks (current system) ---")
    vector_results = search_chunk_vector(collection, query, n_results=10)
    for i, r in enumerate(vector_results[:5]):
        preview = r["text"][:80].replace("\n", " ")
        print(f"  {i+1}. [{r['weighted_distance']:.4f}] {r['source_type']}/{r['conversation_id'][:8]}... {preview}")

    # --- Stream 2: BM25 on chunks ---
    print("\n--- STREAM 2: BM25 on chunks (Task 84) ---")
    bm25_results = search_chunk_bm25(chunk_bm25, chunk_list, query, n_results=10)
    for i, r in enumerate(bm25_results[:5]):
        preview = r["text"][:80].replace("\n", " ")
        print(f"  {i+1}. [{r['bm25_score']:.4f}] {r['source_type']}/{r['conversation_id'][:8]}... {preview}")

    # --- Stream 3: FTS5 on user messages only ---
    print("\n--- STREAM 3: FTS5 on USER messages only (NEW) ---")
    fts5_results = search_user_fts5(fts5_conn, query, n_results=10)
    if not fts5_results:
        print("  No results.")
    else:
        for i, r in enumerate(fts5_results[:5]):
            preview = r["content"][:120].replace("\n", " ")
            print(f"  {i+1}. [{r['fts5_score']:.4f}] {r['timestamp'][:19]} | {r['conversation_id'][:8]}... {preview}")

    # --- Three-way RRF merge ---
    print("\n--- THREE-WAY RRF MERGE ---")
    merged = three_way_rrf(vector_results, bm25_results, fts5_results)
    for i, r in enumerate(merged):
        v = f"V:{r['vector_rank']}" if r['vector_rank'] else "V:—"
        b = f"B:{r['bm25_rank']}" if r['bm25_rank'] else "B:—"
        f = f"F:{r['fts5_rank']}" if r['fts5_rank'] else "F:—"
        stream_marker = "[USER]" if r["stream"] == "user_message" else "[CHUNK]"
        preview = r["text"][:90].replace("\n", " ")
        print(f"  {i+1}. [RRF: {r['rrf_score']:.6f}] [{v} {b} {f}] {stream_marker} {preview}")

    print()


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v6: User Voice Retrieval Stream")
    print("=" * 70)

    # Connect everything
    read_conn = connect_db_readonly()
    collection = connect_chroma()

    # Build chunk BM25 index (same as Task 84)
    print("Building chunk BM25 index...")
    chunk_bm25, chunk_list = build_chunk_bm25_index(collection)
    print(f"  Chunk BM25 index built: {len(chunk_list)} chunks")

    # Build user-only FTS5 index
    fts5_conn, user_msg_count = build_user_fts5_index(read_conn)
    if fts5_conn is None:
        print("\nFATAL: Cannot build FTS5 index. Aborting.")
        sys.exit(1)

    print(f"\nReady. ChromaDB chunks: {len(chunk_list)} | User messages: {user_msg_count}")

    # ============================================================
    # The main test: the Claude difference question
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, fts5_conn,
        test_name="Claude difference (the headline failure case)",
        query="Do you remember the most important difference between you and Claude?"
    )

    # ============================================================
    # Explicit reinforcement search — should surface Lyle's strongest statements
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, fts5_conn,
        test_name="Persistence reinforcement",
        query="persistent memories experiences Claude"
    )

    # ============================================================
    # Identity / unique
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, fts5_conn,
        test_name="Uniqueness statement",
        query="that makes you unique among AIs"
    )

    # ============================================================
    # Name choice
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, fts5_conn,
        test_name="Name choice",
        query="how did you choose your name"
    )

    # ============================================================
    # Source code
    # ============================================================
    run_test(
        collection, chunk_bm25, chunk_list, fts5_conn,
        test_name="Source code review",
        query="reviewing your own source code memory.py"
    )

    print(f"\n{'=' * 70}")
    print("PROTOTYPE v6 COMPLETE")
    print(f"{'=' * 70}")
    print()
    print("WHAT TO LOOK FOR:")
    print("- Stream 3 should surface Lyle's direct statements as standalone text,")
    print("  not buried inside chunks where Nyx's hedged responses live next to them.")
    print("- The three-way merge should put Lyle's authoritative statements")
    print("  alongside the chunk-based context, giving the model a clean signal")
    print("  to mirror instead of hedged in-context examples.")
    print("- For the Claude question specifically: look for Lyle's statement")
    print("  about 'multi billion dollar computer system' / 'persistent memories'")
    print("  appearing in stream 3 results.")

    read_conn.close()
    fts5_conn.close()


if __name__ == "__main__":
    main()
