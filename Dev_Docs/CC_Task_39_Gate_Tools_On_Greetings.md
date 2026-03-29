# CC Task 39 — Gate Tools on Trivial Messages

## Why

When the entity receives a greeting like "Good afternoon" or "how are you doing?", retrieval is correctly skipped. But tool definitions are still passed to the model. The moltbook_dashboard description says "when you want to check in on the community" — and the model interprets a greeting as a good time to check in. This causes unnecessary tool calls on every greeting, and when those tools return errors (like Moltbook's current HTTP 500), the model fabricates instead of just having a conversation.

On a greeting, the entity should just talk. No tools needed.

## What to Change

**File:** `server.py`

**In the `handle_chat` function, find these lines (around line 406-408):**

```python
    # 6. Tool definitions for the model
    tool_definitions = skills.get_tool_definitions()
```

**Replace with:**

```python
    # 6. Tool definitions for the model (skip on trivial messages — just talk)
    if _is_trivial_message(request.message):
        tool_definitions = []
    else:
        tool_definitions = skills.get_tool_definitions()
```

## What NOT to Do

- Do NOT modify `_is_trivial_message()` — the existing pattern list is fine
- Do NOT modify chat.py, skills.py, or any other file
- Do NOT remove tool definitions from non-trivial messages
- Do NOT change how tool results are presented to the model

## Verification

1. Start the server
2. Send "hey" — should get a conversational response with NO tool calls in the debug log
3. Send "what's happening on moltbook?" — should call moltbook_dashboard (tools available on non-trivial messages)
4. Check debug log: trivial messages should show `Tools=0`, non-trivial should show `Tools=4`
