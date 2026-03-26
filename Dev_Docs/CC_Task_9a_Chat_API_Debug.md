# CC Task 9a: Expand Chat API Response With Debug Data

## What This Is

The new UI has a context panel that shows what happened behind the scenes for each message — token breakdown, retrieved memories, search activity. The server already computes all this data for the debug log. This task adds it to the API response so the frontend can display it.

## Changes

### File: `server.py`

**Update the `ChatResponse` model** (approx line 102-107). Change:

```python
class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    memories_used: int
    tools_used: list[str] = []
```

To:

```python
class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    memories_used: int
    tools_used: list[str] = []
    debug: dict = {}
```

**Build the debug data dict and include it in the response.** In `handle_chat()`, after the response logging section (after `debug.log_response(...)`) and before the return statement, add:

```python
    # 13. Build frontend debug data
    frontend_debug = {
        "tokens_used": total_tokens,
        "tokens_headroom": CONTEXT_WINDOW - total_tokens,
        "context_window": CONTEXT_WINDOW,
        "retrieval_skipped": _is_trivial_message(request.message),
        "breakdown": {
            "soul": debug.estimate_tokens(chat.load_soul()),
            "chunks": debug.estimate_tokens(
                "\n".join(c.get("text", "") for c in retrieved_chunks)
            ),
            "summaries": debug.estimate_tokens(
                "\n".join(s.get("content", "") for s in summaries)
            ),
            "skills": debug.estimate_tokens(skill_desc),
            "search": debug.estimate_tokens(search_results) if search_results else 0,
            "history": sum(
                debug.estimate_tokens(m["content"]) for m in trimmed_messages
            ),
        },
        "chunks": [
            {
                "preview": c.get("text", "")[:120],
                "distance": round(c.get("distance", 0), 4),
                "conversation_id": c.get("conversation_id", ""),
            }
            for c in retrieved_chunks
        ],
        "search": {
            "fired": search_results is not None,
            "query": query if should_search and search_type == "search" else "",
            "type": search_type if should_search else "",
            "tokens": debug.estimate_tokens(search_results) if search_results else 0,
        },
        "conversation": {
            "messages_sent": len(trimmed_messages),
            "messages_total": len(conversation_messages),
            "messages_trimmed": len(conversation_messages) - len(trimmed_messages),
        },
    }
```

**Update the return statement** to include the debug data. Change:

```python
    return ChatResponse(
        response=response_text,
        conversation_id=conversation_id,
        memories_used=len(retrieved_chunks),
    )
```

To:

```python
    return ChatResponse(
        response=response_text,
        conversation_id=conversation_id,
        memories_used=len(retrieved_chunks),
        debug=frontend_debug,
    )
```

## What NOT To Do

- Do NOT change the existing debug logging (console + file). This is additive.
- Do NOT change any other endpoints.
- Do NOT change the debug.py file.

## Verification

1. Restart the server.
2. Send a message via the chat UI or curl.
3. Check the JSON response — it should now include a `debug` field with all the breakdown data.
4. Example: `curl -s -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" -d '{"message":"hello"}' | python -m json.tool`
5. Confirm the `debug` field has `tokens_used`, `breakdown`, `chunks`, `search`, `conversation` objects.

## Done When

The `/api/chat` response includes a `debug` field with complete token breakdown, chunk details, and search activity data.
