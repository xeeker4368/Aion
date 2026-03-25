"""
Aion Memory Layer

ChromaDB handles vector search — converting conversation chunks into
searchable embeddings so the entity can find relevant memories.

Two types of chunks:
- Live chunks: created every N messages during active conversation.
  Rough but immediately searchable. Tagged with is_live=true.
- Final chunks: created when a conversation ends. Overlapping windows
  for better retrieval. Replace the live chunks.

The entity can search its memories at any time, even mid-conversation.
"""

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

from config import (
    CHROMA_DIR,
    OLLAMA_HOST,
    EMBED_MODEL,
    LIVE_CHUNK_INTERVAL,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
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

        # Format timestamp as readable
        readable_time = _format_timestamp(timestamp)
        lines.append(f"[{readable_time}] {role}: {content}")
    return "\n".join(lines)


def _format_timestamp(iso_timestamp: str) -> str:
    """Convert ISO timestamp to human-readable format."""
    if not iso_timestamp:
        return "unknown time"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_timestamp)
        return dt.strftime("%B %d, %Y at %I:%M %p")
    except (ValueError, TypeError):
        return iso_timestamp


def should_create_live_chunk(message_count: int) -> bool:
    """Check if it's time to create a live chunk based on message count."""
    return message_count > 0 and message_count % LIVE_CHUNK_INTERVAL == 0


def create_live_chunk(conversation_id: str, messages: list[dict], chunk_index: int):
    """
    Create a live chunk from recent messages during an active conversation.
    These are rough — just enough to be searchable mid-conversation.
    They get replaced by clean final chunks when the conversation ends.
    """
    if not messages:
        return

    collection = _get_collection()
    chunk_id = f"{conversation_id}_live_{chunk_index}"
    text = _messages_to_text(messages)

    # Upsert so we can update if called again with same index
    collection.upsert(
        ids=[chunk_id],
        documents=[text],
        metadatas=[{
            "conversation_id": conversation_id,
            "is_live": "true",
            "chunk_index": chunk_index,
            "message_count": len(messages),
        }],
    )


def create_final_chunks(conversation_id: str, all_messages: list[dict]):
    """
    Create clean overlapping chunks for a completed conversation.
    Removes any live chunks first, then creates proper overlapping windows.
    """
    collection = _get_collection()

    # Remove live chunks for this conversation
    _remove_live_chunks(conversation_id)

    if not all_messages:
        return

    # Create overlapping windows
    chunks = []
    start = 0
    while start < len(all_messages):
        end = min(start + CHUNK_SIZE, len(all_messages))
        chunk_messages = all_messages[start:end]
        chunks.append((start, chunk_messages))

        # Move forward by (chunk_size - overlap)
        step = CHUNK_SIZE - CHUNK_OVERLAP
        if step < 1:
            step = 1
        start += step

        # If we've included the last message, stop
        if end >= len(all_messages):
            break

    # Embed all chunks
    ids = []
    documents = []
    metadatas = []

    for i, (start_idx, chunk_messages) in enumerate(chunks):
        chunk_id = f"{conversation_id}_final_{i}"
        text = _messages_to_text(chunk_messages)

        ids.append(chunk_id)
        documents.append(text)
        metadatas.append({
            "conversation_id": conversation_id,
            "is_live": "false",
            "chunk_index": i,
            "start_message": start_idx,
            "message_count": len(chunk_messages),
        })

    if ids:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)


def _remove_live_chunks(conversation_id: str):
    """Remove all live chunks for a conversation."""
    collection = _get_collection()

    # Query for live chunks belonging to this conversation
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
        # If no results or collection empty, nothing to remove
        pass


def search(query: str, n_results: int = RETRIEVAL_RESULTS,
           exclude_conversation_id: str = None) -> list[dict]:
    """
    Search memory for relevant chunks.

    Returns a list of results, each with:
    - text: the chunk content
    - conversation_id: which conversation it came from
    - distance: how close the match is (lower = better)
    """
    collection = _get_collection()

    # Check if collection has any documents
    if collection.count() == 0:
        return []

    # Build the query
    query_params = {
        "query_texts": [query],
        "n_results": min(n_results, collection.count()),
    }

    # Optionally exclude current conversation from results
    # (we already have it in context)
    if exclude_conversation_id:
        query_params["where"] = {
            "conversation_id": {"$ne": exclude_conversation_id}
        }

    try:
        results = collection.query(**query_params)
    except Exception:
        return []

    # Format results
    memories = []
    if results and results["documents"] and results["documents"][0]:
        for i, doc in enumerate(results["documents"][0]):
            memories.append({
                "text": doc,
                "conversation_id": results["metadatas"][0][i].get("conversation_id", ""),
                "distance": results["distances"][0][i] if results["distances"] else None,
            })

    return memories
