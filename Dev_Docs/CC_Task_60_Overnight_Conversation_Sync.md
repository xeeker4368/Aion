# CC Task 60 — Overnight Conversation Sync Fix

Read this spec. Make exactly these changes. Nothing else.

## Problem

The overnight cycle runs as a separate cron process. It ends all active conversations in SQLite (`ended_at` set). But the server process keeps `_active_conversation_id` in memory. The server never checks the DB — it just sees "not None" and keeps appending messages to a conversation the overnight already closed.

Result: messages pile up in a closed conversation. The overnight only processed the messages that existed when it ran. Post-overnight messages miss observation, research, and journaling until the next overnight cycle.

## Fix

Two files, two changes.

### Change 1: db.py — Add `is_conversation_ended()`

Add this function after `get_active_conversations()` (after line 171):

```python
def is_conversation_ended(conversation_id: str) -> bool:
    """Check if a conversation has been ended (by overnight or other process)."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT ended_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return row is not None and row["ended_at"] is not None
```

### Change 2: server.py — Modify `_ensure_active_conversation()`

Replace the current `_ensure_active_conversation()` function (lines 224-232) with:

```python
def _ensure_active_conversation() -> str:
    """Make sure there's an active conversation. Start one if needed."""
    global _active_conversation_id

    if _active_conversation_id is not None:
        # Check if overnight (or anything else) ended this conversation
        if db.is_conversation_ended(_active_conversation_id):
            logger.info(
                f"Conversation {_active_conversation_id} was ended externally "
                f"(overnight cycle). Chunking stragglers and starting fresh."
            )
            _end_active_conversation()

    if _active_conversation_id is None:
        _active_conversation_id = db.start_conversation()
        logger.info(f"Started conversation {_active_conversation_id}")

    return _active_conversation_id
```

## What This Does

1. On every message, `_ensure_active_conversation()` checks: does the DB still say this conversation is active?
2. If the overnight ended it, call `_end_active_conversation()` — which chunks any remaining messages added after the overnight ran, marks chunked, and clears `_active_conversation_id`.
3. Then the second `if` block sees `None` and starts a fresh conversation.

The DB check is one SELECT by primary key — negligible compared to Ollama inference time.

## What NOT to Do

- Do NOT add a file-based signal or IPC mechanism between overnight and server. The DB is the source of truth.
- Do NOT change `_end_active_conversation()`. It already handles the chunking correctly. When called on a conversation with 51 messages (10 interval), it calculates remaining = 1, chunks that last message, marks chunked, ends conversation. All idempotent.
- Do NOT change overnight.py. The overnight is doing its job correctly.
- Do NOT change any other function or file.

## Verification

1. Start the server normally. Send a message (conversation starts).
2. In a separate terminal, simulate what the overnight does:
   ```python
   python3 -c "
   import db
   db.init_databases()
   active = db.get_active_conversations()
   for c in active:
       db.end_conversation(c['id'])
       print(f'Ended {c[\"id\"]}')
   "
   ```
3. Send another message to the server.
4. Check the server logs. You should see:
   - "Conversation {old_id} was ended externally (overnight cycle). Chunking stragglers and starting fresh."
   - "Started conversation {new_id}"
5. The new message should be in a NEW conversation, not the old one.
6. Check the DB: the old conversation should have `ended_at` set, the new conversation should be active.
