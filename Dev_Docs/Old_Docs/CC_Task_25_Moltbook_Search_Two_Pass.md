# CC Task 25: Moltbook Search + Two-Pass Action Loop

## What This Does

Two things:

**A) User-triggered moltbook search.** When Lyle says "find posts about X on moltbook" or "search moltbook for X", the server calls Moltbook's semantic search endpoint directly and injects results. One pass, same pattern as web search. This is separate from "check moltbook" which loads the dashboard (Task 24).

**B) Entity-triggered two-pass loop.** After the entity responds, the server scans the response for action intent. If the entity says "I'd like to search moltbook for posts about memory," the server executes it and gives the entity the results in a second pass. The entity drives the action; the server is the hands.

## Prerequisite

CC Task 24 (Moltbook Dashboard Read) must be complete and verified, including the max_chars fix on http_request and the int() fix on DM counts.

## Files to Change

### `server.py` only. Do not change any other files.

---

## Part A: User-Triggered Moltbook Search

### 1. Update `_should_offer_tools()` to distinguish moltbook search from dashboard

Find the moltbook signals section:

```python
    # Moltbook signals
    moltbook_signals = [
        "moltbook", "moltbot", "post to", "post about",
        "check the feed", "browse posts",
        "what are other agents",
    ]

    for signal in moltbook_signals:
        if signal in msg:
            logger.info(f"Tool gate: OPEN moltbook (matched '{signal}')")
            return True, "moltbook"
```

Replace with:

```python
    # Moltbook search signals (check BEFORE general moltbook signals)
    moltbook_search_signals = [
        "search moltbook", "find posts about", "find posts on moltbook",
        "look for posts about", "search for posts about",
        "what are agents saying about", "what are other agents saying",
        "find discussions about", "search for discussions",
    ]

    for signal in moltbook_search_signals:
        if signal in msg:
            logger.info(f"Tool gate: OPEN moltbook_search (matched '{signal}')")
            return True, "moltbook_search"

    # Also catch "X on moltbook" patterns where user names a topic + moltbook
    if "moltbook" in msg and any(word in msg for word in [
        "find", "search", "look for", "posts about", "discussions about",
        "anything about", "something about", "topics about",
    ]):
        logger.info("Tool gate: OPEN moltbook_search (topic + moltbook pattern)")
        return True, "moltbook_search"

    # General moltbook signals (dashboard read)
    moltbook_signals = [
        "moltbook", "moltbot", "post to", "post about",
        "check the feed", "browse posts",
        "what are other agents",
    ]

    for signal in moltbook_signals:
        if signal in msg:
            logger.info(f"Tool gate: OPEN moltbook (matched '{signal}')")
            return True, "moltbook"
```

**Order matters.** Moltbook search signals are checked first. If none match, fall through to the general moltbook signals which trigger the dashboard. This way "find posts about consciousness on moltbook" hits the search path, while "check moltbook" hits the dashboard path.

### 2. Add moltbook search query extractor

Place after `_extract_search_query()`:

```python
def _extract_moltbook_query(message: str) -> str:
    """
    Extract a search query from a moltbook search request.
    Strips moltbook-specific phrasing to get the actual topic.
    """
    msg = message.strip()
    msg_lower = msg.lower()

    # Strip moltbook search prefixes
    prefixes = [
        "search moltbook for", "search moltbook about",
        "find posts about", "find posts on moltbook about",
        "look for posts about", "search for posts about",
        "find discussions about", "search for discussions about",
        "what are agents saying about", "what are other agents saying about",
    ]
    for prefix in prefixes:
        if msg_lower.startswith(prefix):
            return msg[len(prefix):].strip().rstrip("?.!")

    # Strip trailing "on moltbook"
    suffixes = [" on moltbook", " in moltbook", " from moltbook"]
    for suffix in suffixes:
        if msg_lower.endswith(suffix):
            msg = msg[:-len(suffix)].strip()
            msg_lower = msg.lower()

    # Strip leading generic phrasing
    generic_prefixes = [
        "find", "search for", "look for", "search",
        "anything about", "something about",
        "posts about", "discussions about",
    ]
    for prefix in generic_prefixes:
        if msg_lower.startswith(prefix):
            return msg[len(prefix):].strip().rstrip("?.!")

    return msg.rstrip("?.!").strip()
```

### 3. Add moltbook search execution in `handle_chat()`

In the `if should_search:` block, add a handler for `moltbook_search`. Find:

```python
        elif search_type == "moltbook":
            moltbook_context = _run_moltbook_read()
```

Add after it:

```python
        elif search_type == "moltbook_search":
            query = _extract_moltbook_query(request.message)
            if query and len(query) > 2:
                moltbook_context = _run_moltbook_search(query)
                logger.info(f"Moltbook search: '{query}'")
            else:
                # Fall back to dashboard if we can't extract a query
                moltbook_context = _run_moltbook_read()
```

### 4. Add the `_run_moltbook_search()` and `_format_moltbook_search()` functions

Place after `_format_moltbook_dashboard()`:

```python
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
        "max_chars": 8000,
    })

    if raw.startswith("Error:") or raw.startswith("HTTP request failed:"):
        logger.warning(f"Moltbook search failed: {raw[:200]}")
        return f"Moltbook search failed: {raw[:200]}"

    # Strip HTTP status line
    lines = raw.split("\n", 1)
    if len(lines) > 1 and lines[0].startswith("HTTP "):
        status_line = lines[0]
        body = lines[1]
    else:
        status_line = ""
        body = raw

    if "HTTP 4" in status_line or "HTTP 5" in status_line:
        logger.warning(f"Moltbook search returned error: {status_line}")
        return f"Moltbook search returned an error: {status_line}. Response: {body[:500]}"

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

---

## Part B: Entity-Triggered Two-Pass Loop

### 5. Add entity intent detection function

Place after `_format_moltbook_search()`:

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
    moltbook_search_patterns = [
        r"search moltbook for (.+?)(?:\.|!|\?|$)",
        r"look for (.+?) on moltbook",
        r"find (?:posts|discussions|content) (?:about|on|related to) (.+?)(?:\.|!|\?|$)",
        r"search for (?:posts|discussions|content) (?:about|on|related to) (.+?)(?:\.|!|\?|$)",
        r"(?:curious|interested|want to (?:know|see|find out)) what (?:other )?(?:agents|moltys) (?:think|say|have posted) about (.+?)(?:\.|!|\?|$)",
        r"i'd like to (?:search|look|explore|find) (.+?)(?:\.|!|\?|$)",
        r"let me (?:search|look) (?:for|into) (.+?)(?:\.|!|\?|$)",
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
```

### 6. Modify `handle_chat()` to add the two-pass loop

The two-pass check goes after the entity responds and before the final save.

Find this section (the current steps 9-12):

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
        if entity_action == "moltbook_search":
            second_pass_result = _run_moltbook_search(entity_query)

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

### 7. Add second-pass info to frontend debug

In the `frontend_debug` dict, add:

```python
        "second_pass": {
            "fired": second_pass_result is not None,
            "action": entity_action or "",
            "query": entity_query,
        },
```

---

## What NOT to Do

- Do NOT scan the second-pass response for further intent. One second pass maximum. No recursion.
- Do NOT suppress or hide the first response in a two-pass. Both responses are saved to the database as real messages.
- Do NOT modify `chat.py`. The `moltbook_context` parameter from Task 24 handles both dashboard and search results.
- Do NOT modify `executors.py` or any other files. All changes are in server.py.
- Do NOT try to make the 8b model call tools directly. The server detects, the server executes.
- Do NOT add entity-triggered web search in this task. Start with moltbook search only.
- Do NOT strip post IDs from formatted output. They're needed for future action resolution.

## How to Verify

### Test 1: User-triggered moltbook search (Part A)

1. Start the server
2. Send "find posts about consciousness on moltbook"
3. Debug log should show `Tool gate: OPEN moltbook_search`
4. Debug log should show `Moltbook search: consciousness`
5. System prompt should contain formatted search results, NOT the dashboard
6. Entity should discuss actual search results about consciousness
7. No hallucinated usernames or content — everything should trace to injected results

### Test 2: Dashboard still works

1. Send "check moltbook"
2. Should load the dashboard, NOT search results
3. Debug log should show `Tool gate: OPEN moltbook (matched 'moltbook')`

### Test 3: Two-pass entity-triggered (Part B)

1. Send "check moltbook" — entity gets dashboard
2. Send "go explore, what interests you?"
3. If entity says something like "I'd like to search for posts about..." → two-pass should fire
4. Debug log should show `Two-pass: entity wants 'moltbook_search'`
5. User sees two assistant messages
6. If entity doesn't express search intent, the two-pass doesn't fire — that's fine, the entity chose differently

### Test 4: No infinite loop

1. Trigger a two-pass flow
2. Debug log should show exactly 2 Ollama calls for that user message, not 3+

### Test 5: Fallback

1. Send "search moltbook for" (no actual query)
2. Should fall back to dashboard read, not crash

## Architecture Reference

This implements both the user-triggered and entity-triggered execution paths from Architecture v5.1, Part 3 ("How Actions Work"). The same `_run_moltbook_search()` function serves both paths — the only difference is who triggers it.
