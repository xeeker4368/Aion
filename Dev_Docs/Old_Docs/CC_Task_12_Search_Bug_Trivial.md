# CC Task 12: Fix Search Confirmation Bug + Trivial Message Patterns

## What This Is

Two bug fixes:

**Bug 1:** The search confirmation system fired on "Sure, what do you want to know?" because "Sure" matched the confirmation signals list. The pending search topic was stale — set many messages earlier. The server searched with a garbage query and injected irrelevant results (Adam Lambert lyrics) into the system prompt.

The root cause: `_pending_search_topic` persists indefinitely. A confirmation 20 messages later still fires. It should expire after 1 turn.

**Bug 2:** "How are you today?" doesn't get caught as trivial and triggers retrieval unnecessarily.

## Changes

### File: `server.py`

**Add a turn counter to expire pending search topics.** Near the top of the file where `_pending_search_topic` is defined (approx line 170), change:

```python
_pending_search_topic: str | None = None
```

To:

```python
_pending_search_topic: str | None = None
_pending_search_turn: int = 0
_message_count_global: int = 0
```

**Update the search execution block** in `handle_chat()` (approx lines 344-368). Replace the entire block:

```python
    # 4. Server-side tool execution (no tools passed to model)
    global _pending_search_topic
    search_results = None
    query = ""

    should_search, search_type = _should_offer_tools(request.message)

    if should_search:
        if search_type == "confirm" and _pending_search_topic:
            # User confirmed a pending search — use the stored topic
            search_results = _run_server_side_search(_pending_search_topic)
            _pending_search_topic = None
        elif search_type == "search":
            query = _extract_search_query(request.message)
            if query:
                # Store as pending topic in case the entity asks first
                # and also search immediately since the signal was explicit
                _pending_search_topic = query
                search_results = _run_server_side_search(query)
    else:
        # No search signal — extract topic from message in case the
        # entity offers to search and the user confirms next turn
        query = _extract_search_query(request.message)
        if query and len(query) > 3:
            _pending_search_topic = query
```

With:

```python
    # 4. Server-side tool execution (no tools passed to model)
    global _pending_search_topic, _pending_search_turn, _message_count_global
    search_results = None
    query = ""
    _message_count_global += 1

    # Expire pending search topic after 1 turn
    if _pending_search_topic and (_message_count_global - _pending_search_turn) > 1:
        logger.info(f"Pending search topic expired: {_pending_search_topic}")
        _pending_search_topic = None

    should_search, search_type = _should_offer_tools(request.message)

    if should_search:
        if search_type == "confirm" and _pending_search_topic:
            # User confirmed a pending search — use the stored topic
            search_results = _run_server_side_search(_pending_search_topic)
            _pending_search_topic = None
        elif search_type == "search":
            query = _extract_search_query(request.message)
            if query and len(query) > 3:
                _pending_search_topic = query
                _pending_search_turn = _message_count_global
                search_results = _run_server_side_search(query)
    else:
        # No search signal — store topic only if the entity might offer to search
        # The topic expires after 1 turn if not confirmed
        query = _extract_search_query(request.message)
        if query and len(query) > 3:
            _pending_search_topic = query
            _pending_search_turn = _message_count_global
```

**Add patterns to `_is_trivial_message()`**. In the `trivial_patterns` list, add these entries:

```python
        # Extended greetings
        "how are you today", "hows it going today",
        "how are you doing", "how are you doing today",
        "how you doing today", "whats going on",
        "what's going on", "not much", "nm",
```

Add them in the greetings section of the list, after the existing "how ya doing" entry.

## What NOT To Do

- Do NOT change the `_should_offer_tools()` function
- Do NOT change the `_extract_search_query()` function
- Do NOT change any other files

## Verification

1. Restart the server.
2. Send "Hey" — should show retrieval skipped, no search.
3. Send "how are you today?" — should show retrieval skipped (new pattern).
4. Send "What do you think about memory systems?" — should trigger retrieval, no search.
5. Send "Sure" — should NOT trigger a search. The pending topic from step 4 should have expired (it's been 1 turn). Console should show "Pending search topic expired" log line.
6. Send "search for Python 3.13 release date" — should trigger search normally.
7. Immediately send "yes" — should confirm the search (within 1 turn). This tests that legitimate confirmations still work.

## Done When

"Sure" in a non-search context doesn't trigger garbage searches. Pending topics expire after 1 turn. "How are you today" is caught as trivial. Legitimate search confirmations still work on the immediately following message.
