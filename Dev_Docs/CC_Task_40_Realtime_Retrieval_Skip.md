# CC Task 40 — Skip Retrieval on Realtime Skill Queries

## Why

When someone asks "what's happening on moltbook?", the server retrieves old memory chunks about Moltbook AND calls the live tool. The old chunks contain fabricated content from earlier sessions. The model sees those fake memories, gets a 500 from the live tool, and pattern-completes from the fabricated memories instead of reporting the error honestly.

Realtime data should come from live tool calls, not from memory. If someone asks "what's on Facebook?" you open the app — you don't recall what you saw three days ago. Same principle.

Also, "hi again" and similar patterns are not in the trivial message list and are triggering retrieval on greetings.

## What to Change

**File:** `server.py`

### Change 1: Add missing trivial patterns

In the `_is_trivial_message` function, find the `trivial_patterns` list. Add these entries to the greetings section:

```python
        "hi again", "hey again", "hello again",
        "hi there again", "hey there again",
        "im back", "i'm back",
```

### Change 2: Add realtime skill detection function

Add this function AFTER `_is_trivial_message` and BEFORE `_detect_ingest`:

```python
def _targets_realtime_skill(message: str) -> bool:
    """
    Detect if a message is asking about a realtime skill's domain.

    Realtime data should come from live tool calls, not from memory.
    When someone asks "what's on moltbook?" they want current data,
    not memories of what was there last week.

    Returns True if retrieval should be skipped in favor of tool calls.
    """
    msg = message.lower().strip()

    # Get realtime skill names
    realtime_skills = [s for s in skills.get_ready_skills() if s.get("realtime")]

    for skill in realtime_skills:
        skill_name = skill["name"].lower()
        if skill_name in msg:
            return True

    return False
```

### Change 3: Use the new function in handle_chat

In the `handle_chat` function, find the retrieval section:

```python
    # 3. Retrieve memories (skip for greetings and trivial messages)
    if _is_trivial_message(request.message):
        retrieved_chunks = []
        logger.info("Retrieval: SKIPPED (trivial message)")
    else:
        retrieved_chunks = memory.search(
```

Replace with:

```python
    # 3. Retrieve memories (skip for greetings and realtime skill queries)
    if _is_trivial_message(request.message):
        retrieved_chunks = []
        logger.info("Retrieval: SKIPPED (trivial message)")
    elif _targets_realtime_skill(request.message):
        retrieved_chunks = []
        logger.info("Retrieval: SKIPPED (realtime skill — use live data)")
    else:
        retrieved_chunks = memory.search(
```

## What NOT to Do

- Do NOT modify skills.py, chat.py, memory.py, or any other file
- Do NOT change how tool definitions are passed
- Do NOT modify the tool execution or error handling
- Do NOT add any behavioral directives to the system prompt

## Verification

1. Restart the server
2. Send "hi again" — debug log should show `RETRIEVAL SKIPPED (trivial message)` and `Tools=0`
3. Send "what's happening on moltbook?" — debug log should show `RETRIEVAL SKIPPED (realtime skill)` and `Chunks=0`
4. Send "tell me about Python" — debug log should show chunks retrieved normally (not a realtime skill query)
