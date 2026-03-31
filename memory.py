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

import chromadb
from datetime import datetime, timezone
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

from config import (
    CHROMA_DIR,
    OLLAMA_HOST,
    EMBED_MODEL,
    LIVE_CHUNK_INTERVAL,
    RETRIEVAL_RESULTS,
)
from utils import format_timestamp

# Module-level state
_client = None
_collection = None


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


def _get_collection():
    """Get the collection, initializing if needed."""
    if _collection is None:
        init_memory()
    return _collection


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

    for i, chunk_text_piece in enumerate(chunks):
        # Prepend title to first chunk for better embedding context
        text_to_store = chunk_text_piece
        if i == 0:
            text_to_store = f"{title}\n\n{chunk_text_piece}"

        chunk_id = f"{doc_id}_chunk_{i}"

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

    return len(chunks)


def search(query: str, n_results: int = RETRIEVAL_RESULTS,
           exclude_conversation_id: str = None) -> list[dict]:
    """
    Search memory for relevant chunks.

    Results are weighted by source trust (firsthand experience ranks
    higher than thirdhand articles) and deduplicated so no single
    conversation dominates the results.

    Returns a list of results, each with:
    - text: the chunk content
    - conversation_id: which conversation it came from
    - distance: how close the match is (lower = better)
    - weighted_distance: distance after trust weighting
    - source_type: what kind of experience
    - source_trust: how trustworthy
    """
    collection = _get_collection()

    if collection.count() == 0:
        return []

    # Request more results than needed so we have room after deduplication
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
        results = collection.query(**query_params)
    except Exception as e:
        import logging
        logging.getLogger("aion.memory").error(f"ChromaDB search failed: {e}")
        return []

    if not results or not results["documents"] or not results["documents"][0]:
        return []

    # Trust weighting — firsthand experience ranks higher
    trust_weights = {
        "firsthand": 0.9,    # boost — conversations with Lyle
        "secondhand": 1.0,   # neutral — research, observations
        "thirdhand": 1.1,    # penalty — articles, other AIs
    }

    memories = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        raw_distance = results["distances"][0][i] if results["distances"] else None

        trust = meta.get("source_trust", "firsthand")
        weight = trust_weights.get(trust, 1.0)
        weighted_distance = raw_distance * weight if raw_distance is not None else None

        memories.append({
            "text": doc,
            "conversation_id": meta.get("conversation_id", ""),
            "distance": raw_distance,
            "weighted_distance": weighted_distance,
            "source_type": meta.get("source_type", "conversation"),
            "source_trust": meta.get("source_trust", "firsthand"),
        })

    # Sort by weighted distance (lower = better)
    memories.sort(key=lambda m: m["weighted_distance"] if m["weighted_distance"] is not None else float("inf"))

    # Deduplicate — max one chunk per conversation
    seen_conversations = set()
    deduplicated = []
    for mem in memories:
        conv_id = mem["conversation_id"]
        if conv_id not in seen_conversations:
            seen_conversations.add(conv_id)
            deduplicated.append(mem)
        if len(deduplicated) >= n_results:
            break

    return deduplicated
