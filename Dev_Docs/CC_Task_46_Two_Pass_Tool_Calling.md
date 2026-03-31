# CC Task 46 — Two-Pass Tool Calling

## What This Is

The entity reflexively calls tools on every message because Ollama fires tool calls whenever tool definitions are present. The fix: don't pass tool definitions unless the entity decides it needs them.

**Pass 1:** Entity responds with NO tool definitions. It sees skill descriptions in the system prompt so it knows what tools exist. If it needs a tool, it says so in its response.

**Pass 2 (only if needed):** Server detects tool intent in the entity's response, discards that response, and re-calls WITH tool definitions. The entity now makes the structured call and gets results.

## Files to Modify

### `server.py` — Replace the tool calling flow in `handle_chat`

Replace lines from `# 6. Tool definitions` through `# 10. Send to model` (the current tool definition gating and the send_message call) with the two-pass approach.

Find this block (approximately lines 410-465, from the tool definitions section through the send_message call):

```python
    # 6. Tool definitions for the model (skip on trivial messages — just talk)
    if _is_trivial_message(request.message):
        tool_definitions = []
    else:
        tool_definitions = executors.get_tool_definitions()

    # 7. Assemble system prompt
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        skill_descriptions=skill_desc,
        ingest_result=ingest_result,
    )

    # 8. Trim conversation for context
    conversation_messages = db.get_conversation_messages(conversation_id)
    trimmed_messages = chat.trim_conversation_for_context(conversation_messages)

    # 9. Debug logging
    total_tokens = debug.estimate_tokens(system_prompt) + sum(
        debug.estimate_tokens(m["content"]) for m in trimmed_messages
    )
    request_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conversation_id": conversation_id,
        "message_number": msg_count,
        "user_message": request.message,
        "retrieval_skipped": _is_trivial_message(request.message),
        "chunks_count": len(retrieved_chunks),
        "chunks_tokens": debug.estimate_tokens(
            "\n".join(c.get("text", "") for c in retrieved_chunks)
        ),
        "skills_tokens": debug.estimate_tokens(skill_desc),
        "tools_count": len(tool_definitions),
        "soul_tokens": debug.estimate_tokens(chat.load_soul()),
        "system_prompt_total_tokens": debug.estimate_tokens(system_prompt),
        "conversation_history_tokens": sum(
            debug.estimate_tokens(m["content"]) for m in trimmed_messages
        ),
        "conversation_messages_sent": len(trimmed_messages),
        "conversation_messages_total": len(conversation_messages),
        "messages_trimmed": len(conversation_messages) - len(trimmed_messages),
        "total_tokens": total_tokens,
        "context_window": CONTEXT_WINDOW,
        "budget_exceeded": total_tokens > CONTEXT_WINDOW,
        "headroom": CONTEXT_WINDOW - total_tokens,
        "system_prompt_full": system_prompt,
    }
    debug.log_request(request_data)

    # 10. Send to model with tool definitions
    response_text, tool_calls_made = chat.send_message(
        system_prompt,
        trimmed_messages,
        tool_definitions=tool_definitions,
        tool_executor=_execute_tool_call,
    )
```

Replace with:

```python
    # 6. Assemble system prompt
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        skill_descriptions=skill_desc,
        ingest_result=ingest_result,
    )

    # 7. Trim conversation for context
    conversation_messages = db.get_conversation_messages(conversation_id)
    trimmed_messages = chat.trim_conversation_for_context(conversation_messages)

    # 8. Debug logging
    total_tokens = debug.estimate_tokens(system_prompt) + sum(
        debug.estimate_tokens(m["content"]) for m in trimmed_messages
    )
    request_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conversation_id": conversation_id,
        "message_number": msg_count,
        "user_message": request.message,
        "retrieval_skipped": _is_trivial_message(request.message),
        "chunks_count": len(retrieved_chunks),
        "chunks_tokens": debug.estimate_tokens(
            "\n".join(c.get("text", "") for c in retrieved_chunks)
        ),
        "skills_tokens": debug.estimate_tokens(skill_desc),
        "tools_count": 0,
        "soul_tokens": debug.estimate_tokens(chat.load_soul()),
        "system_prompt_total_tokens": debug.estimate_tokens(system_prompt),
        "conversation_history_tokens": sum(
            debug.estimate_tokens(m["content"]) for m in trimmed_messages
        ),
        "conversation_messages_sent": len(trimmed_messages),
        "conversation_messages_total": len(conversation_messages),
        "messages_trimmed": len(conversation_messages) - len(trimmed_messages),
        "total_tokens": total_tokens,
        "context_window": CONTEXT_WINDOW,
        "budget_exceeded": total_tokens > CONTEXT_WINDOW,
        "headroom": CONTEXT_WINDOW - total_tokens,
        "system_prompt_full": system_prompt,
    }
    debug.log_request(request_data)

    # 9. Two-pass tool calling
    # Pass 1: No tool definitions — entity responds naturally
    # If it needs tools, it says so. If it doesn't, we're done.
    response_text, tool_calls_made = chat.send_message(
        system_prompt,
        trimmed_messages,
    )

    # Pass 2: If entity expressed tool intent, re-call with tools enabled
    if not _is_trivial_message(request.message) and _has_tool_intent(response_text):
        logger.info("Tool intent detected in response. Re-calling with tools.")
        tool_definitions = executors.get_tool_definitions()
        response_text, tool_calls_made = chat.send_message(
            system_prompt,
            trimmed_messages,
            tool_definitions=tool_definitions,
            tool_executor=_execute_tool_call,
        )
```

Add this function to server.py (near the other helper functions like `_is_trivial_message`):

```python
def _has_tool_intent(response_text: str) -> bool:
    """
    Detect if the entity's response expresses intent to use a tool.

    The entity sees skill descriptions and knows what tools exist.
    When it wants to use one, it mentions it in its response.
    This checks for those signals.
    """
    if not response_text:
        return False

    text = response_text.lower()

    # Tool names the entity knows from skill descriptions
    tool_signals = [
        "web_search", "web_fetch", "http_request", "store_document",
        "let me search", "let me look", "i'll search", "i'll look up",
        "i can search", "i can look up", "let me check",
        "i'll check moltbook", "let me check moltbook",
        "search for", "look up", "look that up",
        "search the web", "check the web",
    ]

    return any(signal in text for signal in tool_signals)
```

### Update `research.py` — Research still needs direct tool calling

Research runs during overnight and needs tools directly (no two-pass). It already calls `chat.send_message` with tool definitions. **No changes needed to research.py** — it bypasses the server's handle_chat entirely.

### Update debug logging

In the `frontend_debug` dict near the end of `handle_chat`, update the tools section to reflect whether the two-pass fired:

Find:
```python
        "tools": {
            "definitions_count": len(tool_definitions),
```

Replace with:
```python
        "tools": {
            "definitions_count": len(tool_calls_made),
```

## What NOT to Do

- Do NOT remove tool definitions from research.py or overnight processes. Two-pass is for conversation only.
- Do NOT modify chat.py or send_message. The two-pass logic lives in server.py.
- Do NOT add behavioral directives about when to use tools.
- Do NOT modify skill descriptions or SKILL.md files.
- Do NOT change the _is_trivial_message function.

## Verification

Restart the server and test:

**Test 1 — Conversational message (should NOT trigger tools):**
Send: "hi" → should get a response with 0 tool calls (trivial, already works)
Send: "can you tell me what you are?" → should get a response with 0 tool calls
Send: "how are you doing today?" → should get a response with 0 tool calls

**Test 2 — Tool-worthy message (should trigger tools):**
Send: "can you search for the latest news about AI?" → entity should express search intent on pass 1, pass 2 fires with tools
Send: "what's on moltbook today?" → entity should express moltbook intent, pass 2 fires

**Test 3 — Check debug log:**
```bash
tail -50 data/logs/debug.log
```
Conversational messages should show Tools=0. Tool-worthy messages should show "Tool intent detected" in the server log.

If conversational messages produce 0 tool calls and tool-worthy messages still work, the task is complete.
