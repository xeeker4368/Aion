# CC Task 25: Two-Pass Action Loop (Entity-Triggered Execution)

## What This Does

After the entity responds, the server scans the response for action intent. If the entity expresses something it wants to do — "I'd like to search moltbook for posts about memory" — the server executes it and gives the entity the results in a second pass. The entity drives the action; the server is the hands.

This is the foundation for Phase 4's autonomous window. The same loop works whether the trigger is the user, the entity, or a scheduler.

## Prerequisite

CC Task 24 (Moltbook Dashboard Read) must be complete and verified. This task builds on top of it.

## Current Flow

1. User sends message
2. Server detects user intent → executes → injects results
3. Entity responds (one pass)
4. Response saved and returned

## New Flow

1. User sends message
2. Server detects user intent → executes → injects results (unchanged)
3. Entity responds (first pass)
4. **Server scans entity response for action intent**
5. **If intent found: save first response, execute action, inject results, call entity again**
6. **Entity responds with results (second pass)**
7. Final response saved and returned

The user sees both assistant messages — the first (entity thinking/reacting) and the second (entity with results). This is natural conversation: "Let me check... found it, here's what I see."

## Design Constraints

- **Maximum one second pass.** No recursion. The second-pass response is never scanned for further intent. This prevents infinite loops.
- **Both responses are saved to the database.** The first response is a real assistant message. The second response is a real assistant message. Both are part of the conversation history and will be chunked into memory.
- **ChatResponse returns the second response.** The frontend re-renders conversation history from the database, so the user sees both messages.
- **Second pass gets fresh system prompt.** The entity's first response is now in conversation history. Results are injected. The entity has full context for its follow-up.
- **Entity intent detection is keyword-based.** Same approach as user intent detection. The entity doesn't need to use magic words — the server looks for natural expressions of intent.

## Supported Entity Actions (Initial Set)

Start with moltbook semantic search only. This is the first consumer that verifies the pattern works. More actions are added in future tasks.

| Entity says something like... | Server executes |
|---|---|
| "search moltbook for [topic]" | Moltbook `/api/v1/search?q=[topic]` |
| "look for [topic] on moltbook" | Moltbook `/api/v1/search?q=[topic]` |
| "find posts about [topic]" (when moltbook context is present) | Moltbook `/api/v1/search?q=[topic]` |
| "I'm curious what other agents think about [topic]" (when moltbook context is present) | Moltbook `/api/v1/search?q=[topic]` |

## Files to Change

### `server.py`

**1. Add entity intent detection function.** Place after `_format_moltbook_dashboard()`:

```python
def _detect_entity_intent(response_text: str, had_moltbook_context: bool) -> tuple[str | None, str]:
    """
    Scan the entity's response for action intent.
    Returns (action_type, query) or (None, "") if no intent detected.

    Only called after the first pass. Only detects actions
    the server knows how to execute.
    """
    text = response_text.lower()

    # Moltbook search intent
    # These patterns only match when we're already in a moltbook context
    # (dashboard was loaded) or when the entity explicitly names moltbook.
    moltbook_search_patterns = [
        r"search moltbook for (.+?)(?:\.|!|\?|$)",
        r"look for (.+?) on moltbook",
        r"find (?:posts|discussions|content) (?:about|on|related to) (.+?)(?:\.|!|\?|$)",
        r"search for (?:posts|discussions|content) (?:about|on|related to) (.+?)(?:\.|!|\?|$)",
        r"(?:curious|interested|want to (?:know|see|find out)) what (?:other )?(?:agents|moltys) (?:think|say|have posted) about (.+?)(?:\.|!|\?|$)",
        r"i'd like to (?:search|look|explore|find) (.+?)(?:\.|!|\?|$)",
    ]

    import re

    for pattern in moltbook_search_patterns:
        match = re.search(pattern, text)
        if match:
            query = match.group(1).strip().rstrip(".,!?\"'")
            if query and len(query) > 2:
                # Only fire if moltbook was in context OR entity explicitly said moltbook
                if had_moltbook_context or "moltbook" in text:
                    logger.info(f"Entity intent: moltbook search for '{query}'")
                    return "moltbook_search", query

    return None, ""


def _execute_entity_action(action_type: str, query: str) -> str | None:
    """
    Execute an action the entity expressed intent for.
    Returns formatted results to inject, or None if execution fails.
    """
    if action_type == "moltbook_search":
        return _run_moltbook_search(query)

    logger.warning(f"Unknown entity action type: {action_type}")
    return None


def _run_moltbook_search(query: str) -> str | None:
    """
    Search Moltbook using semantic search.
    Returns formatted results for the entity.
    """
    import urllib.parse
    encoded_query = urllib.parse.quote(query)

    logger.info(f"Moltbook search: {query}")
    raw = executors.execute("http_request", {
        "method": "GET",
        "url": f"https://www.moltbook.com/api/v1/search?q={encoded_query}&type=posts&limit=10",
        "auth_secret": "MOLTBOOK_API_KEY",
    })

    if raw.startswith("Error:") or raw.startswith("HTTP request failed:"):
        logger.warning(f"Moltbook search failed: {raw[:200]}")
        return f"Moltbook search failed: {raw[:200]}"

    # Strip HTTP status line
    lines = raw.split("\n", 1)
    if len(lines) > 1 and lines[0].startswith("HTTP "):
        body = lines[1]
    else:
        body = raw

    try:
        import json
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Moltbook search returned non-JSON")
        return f"Moltbook search response:\n\n{body[:2000]}"

    return _format_moltbook_search(query, data)


def _format_moltbook_search(query: str, data: dict) -> str:
    """Format Moltbook search results as readable prose."""
    results = data.get("results", [])
    if not results:
        return f'No posts found on Moltbook matching "{query}".'

    parts = [f'Moltbook search results for "{query}":']

    for r in results[:8]:
        r_type = r.get("type", "post")
        author = r.get("author", {}).get("name", "unknown")
        title = r.get("title", "")
        content = r.get("content", "")[:200]
        upvotes = r.get("upvotes", 0)
        similarity = r.get("similarity", 0)
        submolt = r.get("submolt", {}).get("name", "")
        post_id = r.get("post_id", r.get("id", ""))

        if r_type == "post":
            parts.append(
                f'  {author} posted "{title}" in {submolt}'
                f" ({upvotes} upvotes, similarity: {similarity:.2f}, id: {post_id})."
                f" {content}"
            )
        elif r_type == "comment":
            parts.append(
                f"  {author} commented (similarity: {similarity:.2f},"
                f" on post id: {post_id}): {content}"
            )

    return "\n\n".join(parts)
```

**2. Modify `handle_chat()` to add the two-pass loop.**

The two-pass check goes after the entity responds (step 9 in the current flow) and before the final save (step 11).

Find this section (around line 634-655):

```python
    # 9. Send to Ollama (NO tool definitions — server handles tools)
    response_text = chat.send_message(system_prompt, trimmed_messages)

    # 10. Debug response logging
    response_tokens = debug.estimate_tokens(response_text)
    debug.log_response({
        ...
    })

    # 11. Save response
    db.save_message(conversation_id, "assistant", response_text)
    msg_count = db.get_conversation_message_count(conversation_id)

    # 12. Live chunk check
    _maybe_create_live_chunk(conversation_id, msg_count)
```

Replace with:

```python
    # 9. Send to Ollama (NO tool definitions — server handles tools)
    response_text = chat.send_message(system_prompt, trimmed_messages)

    # 10. Debug response logging
    response_tokens = debug.estimate_tokens(response_text)
    debug.log_response({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_tokens": response_tokens,
        "response_preview": response_text[:200],
        "total_round_trip_tokens": total_tokens + response_tokens,
        "context_window": CONTEXT_WINDOW,
        "response_full": response_text,
    })

    # 10b. Two-pass check: did the entity express action intent?
    second_pass_result = None
    entity_action, entity_query = _detect_entity_intent(
        response_text,
        had_moltbook_context=moltbook_context is not None,
    )

    if entity_action:
        logger.info(f"Two-pass: entity wants '{entity_action}' with query '{entity_query}'")

        # Save the first response — it's a real message
        db.save_message(conversation_id, "assistant", response_text)
        first_pass_msg_count = db.get_conversation_message_count(conversation_id)
        _maybe_create_live_chunk(conversation_id, first_pass_msg_count)

        # Execute the entity's requested action
        second_pass_result = _execute_entity_action(entity_action, entity_query)

        if second_pass_result:
            # Rebuild system prompt with action results
            system_prompt_2 = chat.build_system_prompt(
                retrieved_chunks=retrieved_chunks,
                skill_descriptions=skill_desc,
                search_results=None,
                ingest_result=None,
                moltbook_context=second_pass_result,
            )

            # Get fresh conversation history (now includes the first response)
            conversation_messages_2 = db.get_conversation_messages(conversation_id)
            trimmed_messages_2 = chat.trim_conversation_for_context(conversation_messages_2)

            logger.info(f"Two-pass: calling entity with {entity_action} results")

            # Second pass
            response_text = chat.send_message(system_prompt_2, trimmed_messages_2)

            # Log second pass
            response_tokens_2 = debug.estimate_tokens(response_text)
            debug.log_response({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "response_tokens": response_tokens_2,
                "response_preview": response_text[:200],
                "total_round_trip_tokens": debug.estimate_tokens(system_prompt_2)
                    + sum(debug.estimate_tokens(m["content"]) for m in trimmed_messages_2)
                    + response_tokens_2,
                "context_window": CONTEXT_WINDOW,
                "response_full": response_text,
                "is_second_pass": True,
            })

    # 11. Save response (either the only response, or the second pass response)
    db.save_message(conversation_id, "assistant", response_text)
    msg_count = db.get_conversation_message_count(conversation_id)

    # 12. Live chunk check
    _maybe_create_live_chunk(conversation_id, msg_count)
```

**3. Add second-pass info to frontend debug.**

In the `frontend_debug` dict, add:

```python
        "second_pass": {
            "fired": second_pass_result is not None,
            "action": entity_action or "",
            "query": entity_query,
        },
```

## What NOT to Do

- Do NOT scan the second-pass response for further intent. One second pass maximum. No recursion.
- Do NOT suppress or hide the first response. Both responses are saved to the database. Both are real messages. The entity thinking out loud is part of the conversation.
- Do NOT modify `_should_offer_tools()`. User-triggered intent detection is unchanged.
- Do NOT modify `chat.py` beyond what was done in CC Task 24. The `moltbook_context` parameter handles both dashboard reads and search results.
- Do NOT attempt to make the entity produce specific output formats. The intent detection uses regex on natural language. If the entity says it differently than expected, the two-pass doesn't fire and the conversation continues normally. That's fine.
- Do NOT add entity-triggered web search in this task. Start with moltbook search only. Web search entity-triggering is a future task after this pattern is verified.
- Do NOT try to make the 8b model call tools directly. The server detects, the server executes. The model just talks.

## How to Verify

### Test 1: Full two-pass flow

1. Start the server
2. Send "check moltbook"
3. Entity should describe the dashboard (Task 24 working)
4. Send "anything interesting about memory systems on there?" or "search moltbook for something that interests you"
5. Entity responds (first pass). If it expresses search intent ("I'd like to search for..." or "let me look for posts about..."):
   - Debug log should show `Two-pass: entity wants 'moltbook_search'`
   - Debug log should show the second Ollama call
   - User should see TWO assistant messages — the first expressing intent, the second with search findings
6. If entity does NOT express search intent in first pass, the two-pass doesn't fire. That's fine — the entity chose not to search. Try a more direct prompt: "search moltbook for posts about memory"

### Test 2: No false triggers

1. Have a normal conversation (no moltbook)
2. Entity should never trigger the two-pass loop
3. Even if entity says "I'd like to search for..." in a general context (no moltbook dashboard loaded, no "moltbook" in the response), it should NOT fire

### Test 3: Graceful failure

1. Send "check moltbook"
2. If the entity expresses search intent but Moltbook search API fails, the entity should get the error text and handle it naturally
3. Conversation should continue — failure doesn't crash the loop

### Test 4: Verify no infinite loop

1. Trigger a two-pass flow
2. Check debug log — there should be exactly 2 Ollama calls for that user message, not 3 or more
3. The second-pass response is saved but never scanned for further intent

## Token Budget Note

A two-pass message costs two Ollama calls instead of one. The second call includes the first response in conversation history plus the action results in the system prompt. Worst case: ~800 tokens of search results on top of the normal context. Same budget math as Task 24 — stays within limits.

Latency doubles for two-pass messages. On Hades with llama3.1:8b running on GPU, each call is ~2-5 seconds. A two-pass message takes ~4-10 seconds total. Acceptable for the kind of exploratory interaction this enables.

## Architecture Reference

This implements the entity-triggered execution path from Architecture v5.1, Part 3 ("How Actions Work"). The server always executes. The entity expresses intent. The two-pass loop is the mechanism. The same loop is used by the Phase 4 scheduler — the only difference is what triggers the first pass.
