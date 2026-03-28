# CC Task 15: Web Fetch Chaining

## What This Does

When a web search fires, the entity currently gets only Tavily's short snippets (1-2 sentences per result). It has no way to read the actual pages. This change makes the server automatically fetch the top result's full page content after every search, so the entity gets real information instead of just headlines.

## Current Flow

1. Server detects search intent via keyword matching
2. `_run_server_side_search()` calls Tavily → returns titles, URLs, snippets
3. Snippets (truncated to 4000 chars) are injected into the system prompt
4. Entity reads snippets and responds

## New Flow

1. Server detects search intent (unchanged)
2. `_run_server_side_search()` calls Tavily → returns titles, URLs, snippets
3. **Server auto-fetches the top result URL using `_web_fetch()`**
4. **Combined output (snippets + page content) injected into system prompt**
5. Entity reads richer content and responds

## Files to Change

### `config.py`

Add one setting:

```python
# --- Search ---
SEARCH_MONTHLY_LIMIT = 1000  # Tavily free tier (already exists)
SEARCH_FETCH_MAX_CHARS = 4000  # Max chars to include from fetched page
```

### `server.py` — `_run_server_side_search()`

Replace the current function with:

```python
def _run_server_side_search(query: str) -> str:
    """Run a web search server-side and fetch the top result's full page."""
    if not search_limiter.can_search():
        usage = search_limiter.get_usage()
        logger.warning(
            f"Search BLOCKED — monthly limit reached "
            f"({usage['used']}/{usage['limit']})"
        )
        return "Search is unavailable — the monthly search limit has been reached."

    logger.info(f"Server-side search: {query}")
    result = executors.execute("web_search", {"query": query})
    search_limiter.record_search()

    # Chain: fetch the top result's full page for richer context
    fetched = _fetch_top_result(result)
    if fetched:
        result = result + "\n\n--- Full Page Content (top result) ---\n\n" + fetched

    return result
```

Add new helper function:

```python
def _fetch_top_result(search_results: str) -> str | None:
    """
    Extract the first URL from search results and fetch its content.
    Returns the page text, or None if fetch fails or no URL found.
    """
    from config import SEARCH_FETCH_MAX_CHARS

    # Find the first URL in the search results
    for line in search_results.split("\n"):
        if line.startswith("URL: ") and line.strip() != "URL:":
            url = line[5:].strip()
            if url:
                logger.info(f"Fetching top result: {url}")
                content = executors.execute(
                    "web_fetch",
                    {"url": url, "max_chars": SEARCH_FETCH_MAX_CHARS},
                )
                # Don't return error messages as content
                if content and not content.startswith("Failed to fetch"):
                    return content
                else:
                    logger.warning(f"Fetch failed for {url}: {content}")
                    return None
    return None
```

## What NOT to Do

- Do NOT modify `_web_search()` in executors.py. Executors stay generic. Chaining logic belongs in the server.
- Do NOT modify `_web_fetch()` in executors.py.
- Do NOT count web fetches against the Tavily rate limit. Only the Tavily API call counts. Web fetch is a plain HTTP request.
- Do NOT fetch more than one URL. One is enough. We're constrained by context window.
- Do NOT change the Tavily snippet format. The snippets stay as-is. The fetched page content is appended after them.
- Do NOT add any new dependencies.

## How to Verify

1. Start the server
2. Send a message that triggers search (e.g., "search for ChromaDB latest version")
3. Check the debug log:
   - Should see "Server-side search: ..." 
   - Should see "Fetching top result: ..." 
   - Should see the full system prompt in the log file containing both snippets AND fetched page content
4. Entity's response should contain information from the full page, not just the snippet
5. Check that a failed fetch (bad URL, timeout) degrades gracefully — entity still gets the snippets

## Token Budget Note

The combined search output (snippets + fetched page) will be larger than before. Current snippets are ~1000 tokens. Adding 4000 chars of page content adds ~1000 more tokens. Total ~2000 tokens for search.

Current context budget:
- CONTEXT_WINDOW: 10240
- SOUL: 663
- RETRIEVAL: 1500
- RESPONSE: 1000
- CONVERSATION: 7077 (remainder)

Search results are injected into the system prompt but don't have a dedicated budget — they eat into conversation headroom. At ~2000 tokens for search, conversation drops from 7077 to ~5077 when search fires. That's still plenty for a reasonable conversation history.

If this becomes a problem, we add a SEARCH_TOKEN_BUDGET to config.py later. Don't over-engineer it now.
