# CC Task 31 — Document Chunk Format Fix

## Objective

Ingested documents are currently routed through `create_live_chunk()` with a fake message wrapper (`[unknown time] system: ...`). This adds noise to the vector space and violates the Architecture doc's note that "non-conversation documents should use a clean text format without fake message wrapping."

Fix: `ingest_document()` upserts directly to ChromaDB with clean text instead of routing through the conversation chunking path.

## The Change

**File:** `memory.py`
**Function:** `ingest_document()` (lines 170–196)

**Current code:**
```python
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
```

**New code:**
```python
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
```

## What Changed

- `ingest_document()` now upserts directly to ChromaDB instead of routing through `create_live_chunk()`.
- The text is stored as-is — no `_messages_to_text()` call, no `[unknown time] system:` wrapping.
- `message_count` is set to `0` for document chunks (conversations have actual message counts). This distinguishes document chunks from conversation chunks in metadata.
- `chunk_id` format stays the same (`{doc_id}_chunk_{i}`) for consistency.

## What NOT to Do

- Do NOT change `create_live_chunk()`. It is correct for conversation chunks.
- Do NOT change `_messages_to_text()`. It is correct for conversation formatting.
- Do NOT change any other function in memory.py.
- Do NOT change any other file.
- Do NOT add any message wrapping or role prefix to the stored text.

## Verification

1. Restart the server.
2. Ingest a test URL: tell the entity "remember this article https://en.wikipedia.org/wiki/Aion_(deity)" (or any working URL).
3. After ingestion, check what was stored in ChromaDB. Run this in a Python shell:

```python
import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

ef = OllamaEmbeddingFunction(url="http://localhost:11434", model_name="nomic-embed-text")
client = chromadb.PersistentClient(path="data/chromadb")
collection = client.get_or_create_collection("aion_memory", embedding_function=ef)

# Find the most recent document chunks
results = collection.get(where={"source_type": "article"}, include=["documents", "metadatas"])
for i, doc in enumerate(results["documents"]):
    print(f"--- Chunk {i} ---")
    print(doc[:200])
    print(f"Metadata: {results['metadatas'][i]}")
    print()
```

4. **Pass criteria:** The stored text starts with the article title and content — NOT with `[unknown time] system:`.
5. **Bonus check:** `message_count` in metadata should be `0` for document chunks.
