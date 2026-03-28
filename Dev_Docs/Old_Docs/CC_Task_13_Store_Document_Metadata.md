# CC Task 13: Update store_document to Use New Chunking With Metadata

## What This Is

The `_store_document` function in executors.py uses `memory.create_final_chunks()` which is dead code from the old architecture. It needs to use `memory.create_live_chunk()` with the new source metadata fields.

## Changes

### File: `executors.py`

**Replace the `_store_document` function** (approx lines 200-224). Change:

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
    # Import here to avoid circular imports
    import db
    import memory

    # Store as a conversation-like document with a single message
    doc_id = db.start_conversation()
    db.save_message(doc_id, "system", f"[{doc_type}] {title}\n\n{content}")
    db.end_conversation(doc_id)

    # Chunk and embed
    messages = db.get_conversation_messages(doc_id)
    memory.create_final_chunks(doc_id, messages)
    db.mark_conversation_chunked(doc_id)

    return f"Document stored: {title} (type: {doc_type})"
```

To:

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

    return f"Document stored: {title} (type: {doc_type})"
```

## What NOT To Do

- Do NOT change any other functions in executors.py
- Do NOT change the registration block
- Do NOT remove `create_final_chunks` from memory.py — it's dead code but harmless

## Verification

1. Restart the server.
2. Confirm startup is clean — no errors.
3. The store_document executor is used by skills during autonomous window and research, which aren't active yet. Verify the server starts and basic chat works. Full verification happens when document ingestion is tested.

## Done When

Server starts clean, no references to `create_final_chunks` in the active code path.
