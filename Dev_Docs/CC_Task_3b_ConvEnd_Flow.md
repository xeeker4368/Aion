# CC Task 3b: Update Conversation-End Flow

## What This Is

Conversations no longer get chunked into ChromaDB when they end. Instead, they wait for consolidation (a separate batch process). This task updates the conversation-end flow and fixes one query that blocks consolidation from finding pending conversations.

The new `consolidation.py` has already been dropped in. This task makes the rest of the system match.

## Changes

### File 1: `server.py`

**Simplify `_end_active_conversation()`** (approx lines 123-143). Replace the entire function with:

```python
def _end_active_conversation():
    """End the current conversation. Consolidation runs separately."""
    global _active_conversation_id

    if _active_conversation_id is None:
        return

    conv_id = _active_conversation_id
    msg_count = db.get_conversation_message_count(conv_id)

    db.end_conversation(conv_id)

    if msg_count > 0:
        logger.info(f"Conversation {conv_id} ended ({msg_count} messages, pending consolidation)")
    else:
        logger.info(f"Conversation {conv_id} ended (empty)")

    _active_conversation_id = None
```

What was removed: the calls to `db.get_conversation_messages()`, `memory.create_final_chunks()`, and `db.mark_conversation_chunked()`. Conversations no longer get chunked at end — consolidation handles fact extraction later.

**Replace the startup unchunked-conversation handler** (approx lines 68-74). Replace:

```python
    # Chunk any conversations left open from last shutdown
    global _active_conversation_id
    unchunked = db.get_unchunked_ended_conversations()
    for conv in unchunked:
        logger.info(f"Found unchunked conversation {conv['id']}, chunking now...")
        messages = db.get_conversation_messages(conv["id"])
        memory.create_final_chunks(conv["id"], messages)
        db.mark_conversation_chunked(conv["id"])
```

With:

```python
    # Check for conversations that need consolidation
    global _active_conversation_id
    pending = db.get_unconsolidated_conversations()
    if pending:
        logger.info(
            f"{len(pending)} conversations pending consolidation. "
            f"Run 'python consolidation.py' to process them."
        )
```

Consolidation is slow (qwen3:14b batch). We do NOT run it at startup — just log that work is waiting.

### File 2: `db.py`

**Fix `get_unconsolidated_conversations()`** (approx lines 276-283). The current query requires `chunked = 1 AND consolidated = 0`. Since we no longer chunk conversations, the `chunked = 1` condition will never be true and consolidation will never find anything to process. Change:

```python
def get_unconsolidated_conversations() -> list[dict]:
    """Find conversations that are chunked but not yet consolidated."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM conversations "
            "WHERE chunked = 1 AND consolidated = 0",
        ).fetchall()
    return [dict(row) for row in rows]
```

To:

```python
def get_unconsolidated_conversations() -> list[dict]:
    """Find ended conversations that haven't been consolidated yet."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM conversations "
            "WHERE ended_at IS NOT NULL AND consolidated = 0",
        ).fetchall()
    return [dict(row) for row in rows]
```

## What NOT To Do

- Do NOT remove `create_final_chunks()` from memory.py. It's unused now but removing it is a separate cleanup task.
- Do NOT remove `mark_conversation_chunked()` from db.py. Same reason.
- Do NOT remove `get_unchunked_ended_conversations()` from db.py. Same reason.
- Do NOT remove the `chunked` column from the conversations table. Database schema changes happen during the database cleanup task.
- Do NOT touch consolidation.py — it has already been replaced.
- Do NOT run consolidation or modify any data.

## Verification

1. Restart the server: `python server.py`
2. Startup banner should show normally. If there are ended-but-unconsolidated conversations in the database, you should see a log line saying how many are pending consolidation.
3. Send a message in the chat UI. Confirm normal response, no errors.
4. Click "New Conversation" in the UI (or hit the new conversation endpoint).
5. Check console output — should say "ended (N messages, pending consolidation)" with NO mention of chunking.
6. Confirm no new entries were added to ChromaDB for the ended conversation (no final chunks created).

## Done When

Server starts clean, conversations end without chunking, startup logs pending consolidation count instead of chunking old conversations.
