"""
Aion Memory Layer

ChromaDB is the entity's memory. Everything searchable lives here
as conversation chunks — the raw exchanges with full context.

Chunks are created during active conversations every N messages
and stay permanently. When a conversation ends, any remaining
unchunked messages get one final chunk. Nothing is deleted.

All experience types (conversations, research, journals, Moltbook)
flow through the same chunking and embedding pipeline.
"""

import logging
import re

import chromadb
from datetime import datetime, timezone
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
from rank_bm25 import BM25Okapi

logger = logging.getLogger("aion.memory")

from config import (
    CHROMA_DIR,
    OLLAMA_HOST,
    EMBED_MODEL,
    LIVE_CHUNK_INTERVAL,
    RETRIEVAL_RESULTS,
    RETRIEVAL_MAX_DISTANCE,
)
from utils import format_timestamp

EMBED_MAX_CHARS = 8000

# Module-level state
_client = None
_collection = None
_bm25_index = None
_bm25_chunks = []  # List of dicts: {id, text, conversation_id, source_type, source_trust}


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
    _build_bm25_index()


def _get_collection():
    """Get the collection, initializing if needed."""
    if _collection is None:
        init_memory()
    return _collection


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


def _messages_to_text(messages: list[dict]) -> str:
    """
    Convert a list of messages into a readable text block for embedding.
    Preserves who said what and when — context is mandatory.
    Timestamps are formatted as human-readable.
    """
    lines = []
    for msg in messages:
        timestamp = msg.get("timestamp", "")
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        readable_time = format_timestamp(timestamp)
        lines.append(f"[{readable_time}] {role}: {content}")
    return "\n".join(lines)


def should_create_live_chunk(message_count: int) -> bool:
    """Check if it's time to create a live chunk based on message count."""
    return message_count > 0 and message_count % LIVE_CHUNK_INTERVAL == 0


def live_chunk_index(message_count: int) -> int:
    """
    Calculate the chunk index for a live chunk at an interval boundary.

    Chunk numbering is sequential starting from 0:
    - At message 10 (interval=10): index 0 (messages 1-10)
    - At message 20: index 1 (messages 11-20)
    - At message 30: index 2 (messages 21-30)
    """
    return (message_count // LIVE_CHUNK_INTERVAL) - 1


def remainder_chunk_index(message_count: int) -> int:
    """
    Calculate the chunk index for remainder messages at conversation end.

    The remainder chunk gets the next index after the last live chunk:
    - 15 messages (last live chunk at 10 was index 0): remainder is index 1
    - 25 messages (last live chunk at 20 was index 1): remainder is index 2
    """
    return message_count // LIVE_CHUNK_INTERVAL


def create_live_chunk(
    conversation_id: str,
    messages: list[dict],
    chunk_index: int,
    source_type: str = "conversation",
    source_trust: str = "firsthand",
):
    """
    Create a chunk from messages and embed it into ChromaDB.

    Chunks are permanent — they are the entity's memory.

    Args:
        conversation_id: which conversation this came from
        messages: the messages to chunk
        chunk_index: position within the conversation
        source_type: what kind of experience (conversation, research,
                     journal, moltbook, article, creative, observation)
        source_trust: trustworthiness (firsthand, secondhand, thirdhand)
    """
    if not messages:
        return

    collection = _get_collection()
    chunk_id = f"{conversation_id}_chunk_{chunk_index}"
    text = _messages_to_text(messages)

    if len(text) > EMBED_MAX_CHARS:
        logger.warning(f"Chunk text truncated from {len(text)} to {EMBED_MAX_CHARS} chars before embedding.")
        text = text[:EMBED_MAX_CHARS]

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
    _build_bm25_index()



def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    Split text into chunks at paragraph boundaries.

    Tries to break at double newlines (paragraphs), falls back to
    single newlines, falls back to hard cut at chunk_size.
    Each chunk overlaps with the next by `overlap` characters
    to preserve context across boundaries.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            chunks.append(text[start:])
            break

        # Try to break at a paragraph boundary
        break_point = text.rfind("\n\n", start, end)
        if break_point == -1 or break_point <= start:
            # Try single newline
            break_point = text.rfind("\n", start, end)
        if break_point == -1 or break_point <= start:
            # Hard cut
            break_point = end

        # If the break point would produce a chunk smaller than or equal to overlap,
        # use hard cut instead — prevents tiny chunks from code files
        # with lots of short lines, and guarantees forward progress
        if break_point - start <= overlap:
            break_point = end

        chunks.append(text[start:break_point])
        start = break_point - overlap
        if start < 0:
            start = 0

    return chunks


def ingest_document(doc_id: str, text: str, title: str,
                    source_type: str = "article",
                    source_trust: str = "thirdhand") -> int:
    """
    Chunk and embed a document into ChromaDB.

    Documents are stored as clean text — no message wrapping,
    no fake timestamps, no role prefixes. They are not conversations.

    Returns the number of chunks created.
    """
    from config import INGEST_CHUNK_SIZE, INGEST_CHUNK_OVERLAP

    collection = _get_collection()
    chunks = chunk_text(text, INGEST_CHUNK_SIZE, INGEST_CHUNK_OVERLAP)

    successful = 0
    for i, chunk_text_piece in enumerate(chunks):
        # Prepend title to first chunk for better embedding context
        text_to_store = chunk_text_piece
        if i == 0:
            text_to_store = f"{title}\n\n{chunk_text_piece}"

        chunk_id = f"{doc_id}_chunk_{i}"

        if len(text_to_store) > EMBED_MAX_CHARS:
            logger.warning(f"Chunk text truncated from {len(text_to_store)} to {EMBED_MAX_CHARS} chars before embedding.")
            text_to_store = text_to_store[:EMBED_MAX_CHARS]

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

    if successful > 0:
        _build_bm25_index()

    return successful


def create_self_review_chunk(
    message_id: str,
    conversation_id: str,
    review_text: str,
) -> None:
    """
    Index a self-review into ChromaDB as retrievable substrate.

    The review is stored as clean text — no message wrapping, no role
    prefixes, no fake timestamps. It is Nyx's own reasoning about her
    own draft, and it enters the memory system as first-person reflection.

    The draft is NOT indexed. Only the review is retrievable. The draft
    lives in working.db for audit and for future draft->revision training
    data extraction, but the review is the reflective material — it is
    what should surface during future drafts on similar topics.

    Args:
        message_id: the ID of the final assistant message that was sent
        conversation_id: which conversation this review came from
        review_text: the review text itself, as produced by the loop
    """
    if not review_text or not review_text.strip():
        return

    collection = _get_collection()
    chunk_id = f"self_review_{message_id}"

    text_to_store = review_text
    if len(text_to_store) > EMBED_MAX_CHARS:
        logger.warning(
            f"Self-review text truncated from {len(text_to_store)} "
            f"to {EMBED_MAX_CHARS} chars before embedding."
        )
        text_to_store = text_to_store[:EMBED_MAX_CHARS]

    try:
        collection.upsert(
            ids=[chunk_id],
            documents=[text_to_store],
            metadatas=[{
                "conversation_id": conversation_id,
                "message_id": message_id,
                "chunk_index": 0,
                "message_count": 0,
                "source_type": "self_review",
                "source_trust": "self_review",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }],
        )
        _build_bm25_index()
        logger.info(f"Indexed self-review for message {message_id[:8]}")
    except Exception as e:
        logger.error(f"Failed to index self-review for message {message_id}: {e}")


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
        "self_review": 0.7,
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
