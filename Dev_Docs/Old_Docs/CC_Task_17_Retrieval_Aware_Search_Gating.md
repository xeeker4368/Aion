# CC Task 17: Retrieval-Aware Search Gating

## What This Does

Currently, web search and memory retrieval are independent — the server can fire a Tavily search even when ChromaDB already returned strong matches. This wastes searches (1000/month limit) and adds irrelevant noise to the system prompt.

This change makes web search conditional on retrieval quality. If memory already has a strong answer, skip the web search.

## Logic

After retrieval (step 3) and before tool execution (step 4) in `handle_chat()`:
- If any retrieved chunk has a distance score below the confidence threshold → memory has it → skip web search
- If retrieval was skipped (trivial message) → no change, search gating still applies normally
- If retrieval returned no results or weak results → search proceeds as before

This does NOT affect ingestion detection or moltbook detection — only web search.

## Files to Change

### `config.py`

Add:

```python
# --- Retrieval-Aware Search Gating ---
# If any chunk scores below this distance, memory is confident — skip web search.
# Lower distance = closer match. Cosine distance: 0.0 = identical, 2.0 = opposite.
# 0.35 is conservative — only strong matches suppress search.
MEMORY_CONFIDENCE_THRESHOLD = 0.35
```

### `server.py`

Add a helper function:

```python
def _memory_has_answer(retrieved_chunks: list[dict]) -> bool:
    """
    Check if retrieved memory chunks are strong enough to skip web search.
    Returns True if any chunk has a distance score below the confidence threshold.
    """
    from config import MEMORY_CONFIDENCE_THRESHOLD

    for chunk in retrieved_chunks:
        distance = chunk.get("distance")
        if distance is not None and distance < MEMORY_CONFIDENCE_THRESHOLD:
            return True
    return False
```

In `handle_chat()`, modify the search execution block (step 4). Change from:

```python
    if should_search:
        if search_type == "confirm" and _pending_search_topic:
            search_results = _run_server_side_search(_pending_search_topic)
            _pending_search_topic = None

        elif search_type == "search":
            query = _extract_search_query(request.message)
            if query and len(query) > 3:
                search_results = _run_server_side_search(query)
                _pending_search_topic = None
```

To:

```python
    if should_search:
        if search_type == "confirm" and _pending_search_topic:
            search_results = _run_server_side_search(_pending_search_topic)
            _pending_search_topic = None

        elif search_type == "search":
            # Skip web search if memory already has strong results
            if _memory_has_answer(retrieved_chunks):
                logger.info("Search SKIPPED — memory has confident results")
            else:
                query = _extract_search_query(request.message)
                if query and len(query) > 3:
                    search_results = _run_server_side_search(query)
                    _pending_search_topic = None
```

## What NOT to Do

- Do NOT apply this to confirmations (search_type == "confirm"). If the user explicitly asked to search, respect that.
- Do NOT apply this to moltbook signals.
- Do NOT apply this to ingestion detection.
- Do NOT change the retrieval logic, distance calculation, or ChromaDB search. Only gate the web search decision.
- Do NOT change the threshold without testing. 0.35 is conservative — lower it only if too many searches are being skipped incorrectly.

## How to Verify

1. Ingest a Wikipedia article (already done: Large language model)
2. Start a new conversation
3. Ask a question the article covers: "What companies have released large language models?"
4. Check the debug log:
   - Should see retrieved chunks with distance < 0.35
   - Should see "Search SKIPPED — memory has confident results"
   - Should NOT see a Tavily search firing
5. Entity should still answer correctly from memory
6. Then ask something memory doesn't have: "what is the weather in Cleveland"
   - Retrieval should return weak/no results (distance > 0.35)
   - Web search should fire normally
