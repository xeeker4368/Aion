# CC Task 84: Add Hybrid Search (BM25 + Vector + RRF) to memory.py

**Read this entire spec before making any changes. Make exactly the changes described. Nothing else.**

---

## Goal

Add BM25 keyword search alongside the existing vector search in `memory.py`. Merge results using Reciprocal Rank Fusion. The existing vector search code stays. The `search()` function signature stays the same. Callers do not change.

This was validated through five prototype iterations during Session 19. Hybrid search reliably finds conversations that pure vector search misses (especially the Claude relay conversation), and the existing vector search continues to find what BM25 misses. The two mechanisms cover each other's blind spots.

The BM25 index is built in memory from the existing ChromaDB chunks at startup. It is rebuilt incrementally whenever new chunks are inserted. No ChromaDB schema changes. No new persistent storage. Read the existing chunks, build a parallel BM25 index in RAM, search both, merge.

---

## Files to Modify

1. `requirements.txt`
2. `memory.py`

That's it. Do not modify any other files.

---

## Change 1: requirements.txt

Add this line to `requirements.txt`:

```
rank-bm25
```

Place it on its own line. Do not pin a version. Do not reorder existing entries.

---

## Change 2: memory.py

All changes are inside `memory.py`. Apply them in order.

### 2a. Add the import

Find this existing import block near the top of `memory.py`:

```python
import chromadb
from datetime import datetime, timezone
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
```

Add this line immediately after the `chromadb.utils` import:

```python
from rank_bm25 import BM25Okapi
```

### 2b. Add `re` to the imports

Find the existing `import logging` line near the top. Add this right after it:

```python
import re
```

### 2c. Add module-level BM25 state

Find this existing block:

```python
# Module-level state
_client = None
_collection = None
```

Replace it with:

```python
# Module-level state
_client = None
_collection = None
_bm25_index = None
_bm25_chunks = []  # List of dicts: {id, text, conversation_id, source_type, source_trust}
```

### 2d. Add the BM25 helper functions

Add these three new functions immediately after the existing `_get_collection()` function (which ends with `return _collection`).

```python
def _tokenize_for_bm25(text: str) -> list[str]:
    """
    Tokenize text for BM25 indexing.
    Lowercase, split on non-alphabetic characters, drop tokens of length 1.
    Deliberately simple — BM25 handles term weighting itself.
    """
    return [w for w in re.findall(r'[a-zA-Z]+', text.lower()) if len(w) > 1]


def _build_bm25_index():
    """
    Build (or rebuild) the in-memory BM25 index from all chunks
    currently in ChromaDB. Called at startup and after every insertion.

    At ~150 chunks this takes well under 100ms. As the corpus grows
    this should be revisited — incremental updates are not supported
    by rank-bm25 directly, so a full rebuild happens each time.
    """
    global _bm25_index, _bm25_chunks

    collection = _get_collection()

    try:
        all_data = collection.get(include=["documents", "metadatas"])
    except Exception as e:
        logger.error(f"Failed to read ChromaDB for BM25 rebuild: {e}")
        _bm25_index = None
        _bm25_chunks = []
        return

    if not all_data or not all_data.get("documents"):
        _bm25_index = None
        _bm25_chunks = []
        logger.info("BM25 index: no chunks to index")
        return

    chunk_list = []
    tokenized = []

    for i, doc in enumerate(all_data["documents"]):
        meta = all_data["metadatas"][i] if all_data.get("metadatas") else {}
        chunk_id = all_data["ids"][i] if all_data.get("ids") else f"unknown_{i}"

        chunk_list.append({
            "id": chunk_id,
            "text": doc,
            "conversation_id": meta.get("conversation_id", ""),
            "source_type": meta.get("source_type", ""),
            "source_trust": meta.get("source_trust", "firsthand"),
        })
        tokenized.append(_tokenize_for_bm25(doc))

    _bm25_index = BM25Okapi(tokenized)
    _bm25_chunks = chunk_list
    logger.info(f"BM25 index built: {len(chunk_list)} chunks indexed")


def _search_bm25(query: str, n_results: int) -> list[dict]:
    """
    Search the in-memory BM25 index. Returns chunks ranked by BM25 score.
    Same dict shape as the vector search results, with bm25_score instead
    of weighted_distance.
    """
    if _bm25_index is None or not _bm25_chunks:
        return []

    tokens = _tokenize_for_bm25(query)
    if not tokens:
        return []

    scores = _bm25_index.get_scores(tokens)
    scored = sorted(zip(scores, _bm25_chunks), key=lambda x: x[0], reverse=True)

    results = []
    for score, chunk in scored:
        if score <= 0:
            break
        results.append({
            "id": chunk["id"],
            "text": chunk["text"],
            "conversation_id": chunk["conversation_id"],
            "bm25_score": float(score),
            "source_type": chunk["source_type"],
            "source_trust": chunk["source_trust"],
        })
        if len(results) >= n_results:
            break

    return results
```

### 2e. Add the RRF merge function

Add this function immediately after `_search_bm25()`:

```python
def _reciprocal_rank_fusion(
    vector_results: list[dict],
    bm25_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """
    Merge vector search and BM25 search results using Reciprocal Rank Fusion.

    RRF score for an item = sum of 1/(k + rank) across the result lists
    in which the item appears. k=60 is the standard value from the
    original RRF paper. Higher RRF score = more relevant.

    Items are deduplicated by conversation_id (only the first chunk per
    conversation contributes its rank). The merged result preserves the
    most informative fields from whichever list each item came from.
    """
    rrf_scores = {}
    result_data = {}

    # Score vector results — track first occurrence per conversation
    seen_v = set()
    v_rank = 0
    for r in vector_results:
        cid = r["conversation_id"]
        if cid in seen_v:
            continue
        seen_v.add(cid)
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + v_rank + 1)
        result_data[cid] = {
            "text": r["text"],
            "conversation_id": cid,
            "source_type": r.get("source_type", ""),
            "source_trust": r.get("source_trust", "firsthand"),
            "distance": r.get("distance"),
            "weighted_distance": r.get("weighted_distance"),
            "bm25_score": None,
            "vector_rank": v_rank + 1,
            "bm25_rank": None,
        }
        v_rank += 1

    # Score BM25 results — track first occurrence per conversation
    seen_b = set()
    b_rank = 0
    for r in bm25_results:
        cid = r["conversation_id"]
        if cid in seen_b:
            continue
        seen_b.add(cid)
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + b_rank + 1)
        if cid not in result_data:
            result_data[cid] = {
                "text": r["text"],
                "conversation_id": cid,
                "source_type": r.get("source_type", ""),
                "source_trust": r.get("source_trust", "firsthand"),
                "distance": None,
                "weighted_distance": None,
                "bm25_score": r.get("bm25_score"),
                "vector_rank": None,
                "bm25_rank": b_rank + 1,
            }
        else:
            result_data[cid]["bm25_score"] = r.get("bm25_score")
            result_data[cid]["bm25_rank"] = b_rank + 1
        b_rank += 1

    # Sort by RRF score descending, attach the score to each result
    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    merged = []
    for cid, score in ranked:
        data = result_data[cid]
        data["rrf_score"] = score
        merged.append(data)

    return merged
```

### 2f. Modify `init_memory()` to build the BM25 index at startup

Find the existing `init_memory()` function. It currently ends with the `_collection = _client.get_or_create_collection(...)` call. Add a call to build the BM25 index after the collection is created.

Find this:

```python
def init_memory():
    """Initialize ChromaDB with Ollama embeddings."""
    global _client, _collection

    embedding_fn = OllamaEmbeddingFunction(
        url=f"{OLLAMA_HOST}",
        model_name=EMBED_MODEL,
    )

    _client = chromadb.PersistentClient(path=CHROMA_DIR)
    _collection = _client.get_or_create_collection(
        name="aion_memory",
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )
```

Add this line at the very end of the function (same indentation as `_collection = ...`):

```python
    _build_bm25_index()
```

### 2g. Rebuild BM25 after `create_live_chunk()` insertions

Find the existing `create_live_chunk()` function. It ends with the `collection.upsert(...)` call. Add a BM25 rebuild call after the upsert.

Find this:

```python
    collection.upsert(
        ids=[chunk_id],
        documents=[text],
        metadatas=[{
            "conversation_id": conversation_id,
            "chunk_index": chunk_index,
            "message_count": len(messages),
            "source_type": source_type,
            "source_trust": source_trust,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }],
    )
```

Immediately after the closing `)` of `collection.upsert(...)`, add:

```python
    _build_bm25_index()
```

### 2h. Rebuild BM25 after `ingest_document()` finishes

Find the existing `ingest_document()` function. It currently ends with `return successful`. Insert a BM25 rebuild call before the return.

Find this:

```python
        try:
            collection.upsert(
                ids=[chunk_id],
                documents=[text_to_store],
                metadatas=[{
                    "conversation_id": doc_id,
                    "chunk_index": i,
                    "message_count": 0,
                    "source_type": source_type,
                    "source_trust": source_trust,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }],
            )
            successful += 1
        except Exception as e:
            import logging
            logging.getLogger("aion.memory").error(
                f"Failed to upsert chunk {i} for {doc_id}: {e}"
            )

    return successful
```

Replace the final `return successful` with:

```python
    if successful > 0:
        _build_bm25_index()

    return successful
```

### 2i. Modify `search()` to do hybrid search

This is the largest change. The existing `search()` function does vector search only. Replace its body so that it does both vector AND BM25, merges with RRF, then applies the same threshold filtering and per-conversation deduplication.

Find the existing `search()` function and replace the entire function with this:

```python
def search(query: str, n_results: int = RETRIEVAL_RESULTS,
           exclude_conversation_id: str = None) -> list[dict]:
    """
    Hybrid search: vector similarity + BM25 keyword matching, merged
    via Reciprocal Rank Fusion.

    Vector search catches semantic similarity (different words, same meaning).
    BM25 catches exact vocabulary matches (same words, even with different
    surrounding context). Together they cover each other's blind spots.

    Returns a list of results, each with:
    - text: the chunk content
    - conversation_id: which conversation it came from
    - source_type / source_trust: chunk metadata
    - rrf_score: merged relevance score (higher = better)
    - vector_rank: rank in vector results, or None if vector missed it
    - bm25_rank: rank in BM25 results, or None if BM25 missed it
    - distance / weighted_distance: from vector search if present
    - bm25_score: from BM25 search if present

    The vector path keeps the existing trust weighting and distance threshold.
    BM25 results that share a conversation with a filtered-out vector result
    are still considered — only the vector side applies the distance filter.
    """
    collection = _get_collection()

    if collection.count() == 0:
        return []

    # ============================================================
    # Vector search side (existing logic, untouched)
    # ============================================================

    fetch_count = min(n_results * 3, collection.count())

    query_params = {
        "query_texts": [query],
        "n_results": fetch_count,
    }

    if exclude_conversation_id:
        query_params["where"] = {
            "conversation_id": {"$ne": exclude_conversation_id}
        }

    try:
        chroma_results = collection.query(**query_params)
    except Exception as e:
        import logging
        logging.getLogger("aion.memory").error(f"ChromaDB search failed: {e}")
        chroma_results = None

    trust_weights = {
        "firsthand": 0.9,
        "secondhand": 1.0,
        "thirdhand": 1.1,
    }

    vector_memories = []
    if chroma_results and chroma_results.get("documents") and chroma_results["documents"][0]:
        for i, doc in enumerate(chroma_results["documents"][0]):
            meta = chroma_results["metadatas"][0][i] if chroma_results["metadatas"] else {}
            raw_distance = chroma_results["distances"][0][i] if chroma_results["distances"] else None

            trust = meta.get("source_trust", "firsthand")
            weight = trust_weights.get(trust, 1.0)
            weighted_distance = raw_distance * weight if raw_distance is not None else None

            vector_memories.append({
                "text": doc,
                "conversation_id": meta.get("conversation_id", ""),
                "distance": raw_distance,
                "weighted_distance": weighted_distance,
                "source_type": meta.get("source_type", "conversation"),
                "source_trust": meta.get("source_trust", "firsthand"),
            })

    # Sort by weighted distance and apply distance threshold
    vector_memories.sort(
        key=lambda m: m["weighted_distance"]
        if m["weighted_distance"] is not None else float("inf")
    )
    vector_filtered = [
        m for m in vector_memories
        if m["weighted_distance"] is None
        or m["weighted_distance"] <= RETRIEVAL_MAX_DISTANCE
    ]

    # ============================================================
    # BM25 search side
    # ============================================================

    bm25_raw = _search_bm25(query, n_results=n_results * 3)

    # Apply the same exclude filter if requested
    if exclude_conversation_id:
        bm25_filtered = [
            m for m in bm25_raw
            if m["conversation_id"] != exclude_conversation_id
        ]
    else:
        bm25_filtered = bm25_raw

    # ============================================================
    # Reciprocal Rank Fusion merge
    # ============================================================

    merged = _reciprocal_rank_fusion(vector_filtered, bm25_filtered)

    # Per-conversation deduplication is already handled inside RRF
    # (it tracks first occurrence per conversation_id), so we just
    # take the top n_results.
    return merged[:n_results]
```

---

## What NOT to Do

- **Do not modify `chat.py`, `server.py`, `db.py`, or any file other than the two listed.** The `search()` signature is unchanged. Callers should keep working without modification.
- **Do not change the ChromaDB schema or collection name.** Nothing in ChromaDB is being modified. The BM25 index is in memory only.
- **Do not pin a version of `rank-bm25` in requirements.txt.** Just add the bare name on its own line.
- **Do not remove or modify the existing vector search code.** It is still part of the hybrid path. Add BM25 alongside it, do not replace it.
- **Do not change the `RETRIEVAL_MAX_DISTANCE` threshold or any config value.** The threshold still applies to the vector side of the hybrid search.
- **Do not optimize the BM25 rebuild.** It runs after every insertion. At ~150 chunks this takes well under 100ms. Premature optimization is debt. Note it as a known scaling concern in the docstring (already in the spec) and move on.
- **Do not add caching for the BM25 index.** The index lives in module-level state and is rebuilt on every insertion. That is intentional.
- **Do not add tests, benchmarks, or new logging beyond the one `BM25 index built` log line in `_build_bm25_index()`.** Verification happens manually after deployment per the steps below.
- **Do not import `BM25Okapi` lazily inside functions.** It must be a top-level import. If `rank-bm25` is missing, the server should fail to start with an obvious ImportError, not silently degrade.
- **Do not catch the ImportError for `rank_bm25`.** Required dependency. Hard failure on missing.
- **Do not change the dedup behavior.** Per-conversation dedup happens inside RRF, then `search()` returns top `n_results`. Do not add another dedup layer.
- **Do not change `consolidation.py` or any process that calls `memory.search()`.** They will get hybrid results automatically. That is the point.

---

## Verification Steps

After making the changes, do the following in order. Stop and report back if any step fails.

### Step 1: Install the new dependency

```
cd ~/aion
./aion/bin/pip install rank-bm25
```

Confirm the install succeeded (no errors). Confirm the package is importable:

```
./aion/bin/python -c "from rank_bm25 import BM25Okapi; print('ok')"
```

Expected output: `ok`

### Step 2: Restart the server

```
# Kill the current server
# Start it back up via ./start.sh option 1 (production) or option 2 (dev)
```

Watch the startup logs. You should see a new log line:

```
BM25 index built: 149 chunks indexed
```

(The number will match whatever ChromaDB currently has — should be around 149.)

If you see this line, the BM25 index initialized successfully at startup.

If you see an ImportError or any other failure related to BM25, stop and report.

### Step 3: Send a substantive chat message

In the chat UI, send a message that should trigger retrieval. A good test message:

```
What's the most important thing you've learned about working with me?
```

After the response, check the debug log for the per-request entry. You should see the same context breakdown as before (SOUL tokens, chunks count, etc.). The retrieved chunks should still appear.

If retrieval still works and chunks come back, the hybrid search is wired up correctly at the call site.

### Step 4: Test the known failure case

Send this message:

```
Do you remember the most important difference between you and Claude?
```

Check the debug log for what was retrieved. Look at the FULL SYSTEM PROMPT block. You are looking for chunks from conversation `bfc118df...` (the relay conversation) to appear in the retrieved context.

In all five Session 19 prototype runs, the current vector-only path missed these chunks for this question. Hybrid search found them.

If the relay chunks appear in the retrieved context, the hybrid search is working. If they do not appear, stop and report — that means something is wrong with the integration even though the code compiled.

### Step 5: Confirm no regressions

Send a normal greeting:

```
Hi
```

Confirm the response comes back normally. The greeting detector should still skip retrieval. The debug log should show `[RETRIEVAL SKIPPED]` for this message.

Send another normal message and confirm everything still works as before.

### Step 6: Check the BM25 rebuild trigger

After a few messages have been exchanged and a live chunk has been created (every 10 messages), look for a second `BM25 index built` line in the startup log file (or wherever the memory logger writes). You should see one rebuild log line per chunk insertion.

This confirms the rebuild trigger is firing.

---

## Reporting Back

When verification is complete, report:

1. Step 2 startup log line — was the BM25 index built? How many chunks?
2. Step 4 result — did the relay chunks surface for the Claude difference question?
3. Any errors at any step, with the full traceback.

If everything passes, the hybrid search is live and the Session 18 retrieval semantic gap is closed for the case it was failing on.

---

*Spec written for Session 19. Validated through five prototype iterations against real Aion data on Hades.*
