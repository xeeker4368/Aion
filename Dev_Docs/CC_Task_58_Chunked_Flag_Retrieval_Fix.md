# CC Task 58: Fix Chunked Flag + Remove Retrieval Exclusion

**Priority:** Immediate — retrieval exclusion defeats the purpose of live chunking
**Risk:** Low — one flag fix, one filter removal
**Files to modify:** server.py, overnight.py, memory.py

---

## Part 1: Fix Chunked Flag

### The Problem

`mark_conversation_chunked()` is only called when a conversation ends with remainder messages (`msg_count % LIVE_CHUNK_INTERVAL > 0`). If a conversation is exactly 10, 20, 30... messages, `remaining` is 0, the `if` block is skipped, and the `chunked` flag stays at 0 — even though live chunks were created during the conversation.

Example: an 80-message conversation has 8 live chunks in ChromaDB but `chunked: 0` in the database.

### The Fix

Move `mark_conversation_chunked()` outside the `if remaining > 0` block in all three locations.

**server.py — `_end_active_conversation()` (around line 153):**

Current:
```python
    if msg_count > 0:
        # Chunk any messages beyond the last live chunk boundary
        remaining = msg_count % LIVE_CHUNK_INTERVAL
        if remaining > 0:
            messages = db.get_conversation_messages(conv_id)
            chunk_messages = messages[-remaining:]
            chunk_index = memory.remainder_chunk_index(msg_count)

            memory.create_live_chunk(conv_id, chunk_messages, chunk_index)
            db.mark_conversation_chunked(conv_id)
            logger.info(
                f"Final chunk {chunk_index} created for conversation {conv_id} "
                f"({remaining} remaining messages)"
            )

        db.end_conversation(conv_id)
```

Replace with:
```python
    if msg_count > 0:
        # Chunk any messages beyond the last live chunk boundary
        remaining = msg_count % LIVE_CHUNK_INTERVAL
        if remaining > 0:
            messages = db.get_conversation_messages(conv_id)
            chunk_messages = messages[-remaining:]
            chunk_index = memory.remainder_chunk_index(msg_count)

            memory.create_live_chunk(conv_id, chunk_messages, chunk_index)
            logger.info(
                f"Final chunk {chunk_index} created for conversation {conv_id} "
                f"({remaining} remaining messages)"
            )

        # Always mark as chunked — live chunks were created during the conversation
        db.mark_conversation_chunked(conv_id)
        db.end_conversation(conv_id)
```

**overnight.py — `_end_active_conversations()` (around line 51):**

Current:
```python
        if msg_count > 0:
            messages = db.get_conversation_messages(conv_id)
            remaining = msg_count % LIVE_CHUNK_INTERVAL
            if remaining > 0:
                chunk_messages = messages[-remaining:]
                chunk_index = memory.remainder_chunk_index(msg_count)
                memory.create_live_chunk(conv_id, chunk_messages, chunk_index)
                db.mark_conversation_chunked(conv_id)

        db.end_conversation(conv_id)
```

Replace with:
```python
        if msg_count > 0:
            messages = db.get_conversation_messages(conv_id)
            remaining = msg_count % LIVE_CHUNK_INTERVAL
            if remaining > 0:
                chunk_messages = messages[-remaining:]
                chunk_index = memory.remainder_chunk_index(msg_count)
                memory.create_live_chunk(conv_id, chunk_messages, chunk_index)

            # Always mark as chunked — live chunks were created during the conversation
            db.mark_conversation_chunked(conv_id)

        db.end_conversation(conv_id)
```

**server.py — lifespan startup (around line 74):**

This one is different — it handles conversations that already ended but weren't chunked. After the fix above, this path should rarely trigger. But for safety, add a mark after the remainder check:

Current:
```python
    ended_unchunked = db.get_unchunked_ended_conversations()
    for conv in ended_unchunked:
        conv_msg_count = conv.get("message_count", 0)
        remaining = conv_msg_count % LIVE_CHUNK_INTERVAL
        if remaining > 0:
            messages = db.get_conversation_messages(conv["id"])
            if messages:
                chunk_messages = messages[-remaining:]
                chunk_index = memory.remainder_chunk_index(conv_msg_count)
                memory.create_live_chunk(conv["id"], chunk_messages, chunk_index)
                db.mark_conversation_chunked(conv["id"])
                logger.info(
                    f"Startup: chunked {remaining} remaining messages for {conv['id']}"
                )
```

Replace with:
```python
    ended_unchunked = db.get_unchunked_ended_conversations()
    for conv in ended_unchunked:
        conv_msg_count = conv.get("message_count", 0)
        remaining = conv_msg_count % LIVE_CHUNK_INTERVAL
        if remaining > 0:
            messages = db.get_conversation_messages(conv["id"])
            if messages:
                chunk_messages = messages[-remaining:]
                chunk_index = memory.remainder_chunk_index(conv_msg_count)
                memory.create_live_chunk(conv["id"], chunk_messages, chunk_index)
                logger.info(
                    f"Startup: chunked {remaining} remaining messages for {conv['id']}"
                )
        # Always mark as chunked — live chunks may have been created during the conversation
        db.mark_conversation_chunked(conv["id"])
```

---

## Part 2: Remove Retrieval Exclusion

### The Problem

`memory.search()` excludes all chunks from the current conversation. This means:
- In a long conversation, messages that roll off the context window exist as live chunks in ChromaDB but CANNOT be retrieved — the entity loses access to its own conversation history.
- This defeats the entire purpose of live chunking, which exists specifically so trimmed messages stay searchable.

The exclusion was originally added to prevent the entity from echoing what was just said. But the model is smart enough to handle seeing the same content in both retrieved memories and conversation history (Principle 10). And the cost of NOT retrieving — losing access to earlier parts of a long conversation — is far worse than the minor redundancy of occasionally retrieving something still in context.

### The Fix

**server.py (around line 416):**

Current:
```python
        retrieved_chunks = memory.search(
            query=request.message,
            exclude_conversation_id=conversation_id,
        )
```

Replace with:
```python
        retrieved_chunks = memory.search(
            query=request.message,
        )
```

That's it. The `exclude_conversation_id` parameter can stay in `memory.search()` — it does no harm as an unused optional parameter, and it might be useful for other callers in the future. Just stop passing it from the chat handler.

---

## What NOT to Do

- Do NOT remove the `exclude_conversation_id` parameter from `memory.search()`. Leave it as an optional parameter.
- Do NOT change any chunking logic, retrieval ranking, or deduplication.
- Do NOT change the overnight cycle, observer, journal, or research modules.
- Do NOT change ChromaDB data — the existing chunks are correct.

---

## Verification

### Part 1 — Chunked flag:
```bash
# Start a conversation and send exactly 10 messages (5 user + 5 assistant)
# Then start a new conversation (which ends the first one)
# Check the flag:
python3 -c "
import db
db.init_databases()
convs = db.get_recent_conversations()
for c in convs:
    print(f'{c[\"id\"][:12]}... msgs={c[\"message_count\"]} chunked={c[\"chunked\"]}')
"
# The ended conversation should show chunked=1
```

### Part 2 — Retrieval:
```bash
# In the new conversation, ask about something from the previous conversation.
# Check the server log — it should show retrieved chunks WITH the previous conversation's ID.
# The debug panel should show memories > 0.
# In a LONG conversation (20+ messages), early messages that get trimmed from history
# should still be retrievable via ChromaDB.
```

### Fix existing data:
```bash
# The first conversation (8e741d6e...) has chunked=0 despite 8 chunks in ChromaDB.
# Run this one-time fix:
python3 -c "
import db
db.init_databases()
db.mark_conversation_chunked('8e741d6e-0890-4940-9992-e186d92c0225')
print('Fixed chunked flag for first conversation.')
"
```
