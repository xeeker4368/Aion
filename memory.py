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

        readable_time = _format_timestamp(timestamp)
        lines.append(f"[{readable_time}] {role}: {content}")
    return "\n".join(lines)


def _format_timestamp(iso_timestamp: str) -> str:
    """Convert ISO timestamp to human-readable format."""
    if not iso_timestamp:
        return "unknown time"
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        return dt.strftime("%B %d, %Y at %I:%M %p")
    except (ValueError, TypeError):
        return iso_timestamp


def should_create_live_chunk(message_count: int) -> bool:
    """Check if it's time to create a live chunk based on message count."""
    return message_count > 0 and message_count % LIVE_CHUNK_INTERVAL == 0


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


def _remove_live_chunks(conversation_id: str):
    """Remove all live chunks for a conversation."""
    collection = _get_collection()

    try:
        results = collection.get(
            where={
                "$and": [
                    {"conversation_id": {"$eq": conversation_id}},
                    {"is_live": {"$eq": "true"}},
                ]
            }
        )
        if results["ids"]:
            collection.delete(ids=results["ids"])
    except Exception:
        pass


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

    Returns the number of chunks created.
    """
    from config import INGEST_CHUNK_SIZE, INGEST_CHUNK_OVERLAP

    chunks = chunk_text(text, INGEST_CHUNK_SIZE, INGEST_CHUNK_OVERLAP)

    for i, chunk_text_piece in enumerate(chunks):
        # Prepend title to first chunk for better embedding context
        text_to_store = chunk_text_piece
        if i == 0:
            text_to_store = f"{title}\n\n{chunk_text_piece}"

        create_live_chunk(
            conversation_id=doc_id,
            messages=[{"role": "system", "content": text_to_store, "timestamp": ""}],
            chunk_index=i,
            source_type=source_type,
            source_trust=source_trust,
        )

    return len(chunks)


def search(query: str, n_results: int = RETRIEVAL_RESULTS,
           exclude_conversation_id: str = None) -> list[dict]:
    """
    Search memory for relevant chunks.

    Returns a list of results, each with:
    - text: the chunk content
    - conversation_id: which conversation it came from
    - distance: how close the match is (lower = better)
    - source_type: what kind of experience
    - source_trust: how trustworthy
    """
    collection = _get_collection()

    if collection.count() == 0:
        return []

    query_params = {
        "query_texts": [query],
        "n_results": min(n_results, collection.count()),
    }

    if exclude_conversation_id:
        query_params["where"] = {
            "conversation_id": {"$ne": exclude_conversation_id}
        }

    try:
        results = collection.query(**query_params)
    except Exception:
        return []

    memories = []
    if results and results["documents"] and results["documents"][0]:
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            memories.append({
                "text": doc,
                "conversation_id": meta.get("conversation_id", ""),
                "distance": results["distances"][0][i] if results["distances"] else None,
                "source_type": meta.get("source_type", "conversation"),
                "source_trust": meta.get("source_trust", "firsthand"),
            })

    return memories
