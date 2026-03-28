# CC Task 20: Fix store_document Executor — Stop Creating Fake Conversations

## Overview

The `_store_document()` executor in `executors.py` stores documents by creating fake conversations in DB1 and DB2. This is wrong. Documents should use the same storage path as URL ingestion: `memory.ingest_document()` for ChromaDB chunks, `db.save_document()` for the DB2 metadata record.

## The Problem

Current code in `executors.py` `_store_document()`:

```python
# Store in DB1+DB2 as ground truth
doc_id = db.start_conversation()
db.save_message(doc_id, "system", f"[{doc_type}] {title}\n\n{content}")
db.end_conversation(doc_id)

# Chunk and embed with source metadata
messages = db.get_conversation_messages(doc_id)
memory.create_live_chunk(
    conversation_id=doc_id,
    messages=messages,
    chunk_index=0,
    source_type=doc_type,
    source_trust=source_trust,
)
```

This creates a fake "conversation" with a single system message. The document shows up in `get_recent_conversations()` as if it were a real conversation. It pollutes the conversation list. It writes to DB1 (the sacred conversation archive) with data that is not a conversation.

Meanwhile, URL ingestion in `server.py` `_ingest_url()` does it correctly:

```python
chunk_count = memory.ingest_document(
    doc_id=doc_id,
    text=content,
    title=title,
    source_type="article",
    source_trust="thirdhand",
)

db.save_document(
    doc_id=doc_id,
    title=title,
    url=url,
    source_type="article",
    source_trust="thirdhand",
    chunk_count=chunk_count,
)
```

Two ingestion paths, two different storage patterns. They should be one pattern.

## The Fix

Replace the body of `_store_document()` in `executors.py`. Keep the function signature, the docstring, and the trust_map. Replace everything after the trust_map.

**Change to:**

```python
def _store_document(doc_type: str, title: str, content: str) -> str:
    """
    Store a document in the memory system.
    Used by skills that produce knowledge worth remembering.

    Args:
        doc_type: Type of document (research, article, diagnostic, moltbook, etc.)
        title: A short title for the document
        content: The document content
    """
    import uuid
    import db
    import memory

    # Map doc_type to source trust level
    trust_map = {
        "journal": "firsthand",
        "creative": "firsthand",
        "diagnostic": "firsthand",
        "observation": "secondhand",
        "research": "secondhand",
        "moltbook": "thirdhand",
        "article": "thirdhand",
    }
    source_trust = trust_map.get(doc_type, "secondhand")

    doc_id = str(uuid.uuid4())

    # Chunk and embed into ChromaDB (same path as URL ingestion)
    chunk_count = memory.ingest_document(
        doc_id=doc_id,
        text=content,
        title=title,
        source_type=doc_type,
        source_trust=source_trust,
    )

    # Record in DB2 for UI and batch summarization
    db.save_document(
        doc_id=doc_id,
        title=title,
        source_type=doc_type,
        source_trust=source_trust,
        chunk_count=chunk_count,
    )

    return f"Document stored: {title} (type: {doc_type}, {chunk_count} chunks)"
```

## Why No DB1 Write

DB1 is the conversation archive — append-only storage for conversations the entity has had. Documents are not conversations. The conversation that *triggered* document creation (user asked entity to research something, entity produced results) is already in DB1 as part of that conversation's messages.

The document's content lives in ChromaDB chunks (searchable) and its metadata lives in DB2's documents table (for the UI). That matches the URL ingestion pattern and is sufficient.

## What NOT to Do

- Do NOT change the function signature or return type.
- Do NOT change the trust_map values.
- Do NOT modify `_ingest_url()` in server.py — it already works correctly.
- Do NOT modify `db.save_document()` — it already works correctly.
- Do NOT attempt to clean up existing fake conversations — `_store_document` has never been called in production (no server-side trigger exists for it). There is no existing pollution.
- Do NOT address the summaries table overloading in this task (document summaries and conversation summaries sharing the same table). That is a separate issue.

## How to Verify

1. Start the server — should start without errors.
2. Normal conversation, search, and URL ingestion should still work.
3. Code review: confirm `_store_document` no longer calls `db.start_conversation()`, `db.save_message()`, or `db.end_conversation()`.
4. Grep check: `grep -n "start_conversation\|save_message\|end_conversation" executors.py` should return zero results.
