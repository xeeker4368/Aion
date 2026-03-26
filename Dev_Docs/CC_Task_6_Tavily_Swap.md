# CC Task 6: Swap Web Search to Tavily

## What This Is

Replace DuckDuckGo web search with Tavily. Tavily is purpose-built for AI agents — cleaner results, structured JSON, better relevance. Free tier: 1000 searches/month.

Three files change. One new skill replaces the old one. One dependency swapped.

## Changes

### File 1: `executors.py`

**Replace the `_web_search` function** (approx lines 171-197). Delete the entire function and replace with:

```python
def _web_search(query: str, max_results: int = 5) -> str:
    """
    Search the web using Tavily. Requires TAVILY_API_KEY in vault.

    Args:
        query: The search query
        max_results: Maximum number of results (default 5)
    """
    api_key = vault.get("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not found. Add it in Settings (/settings)."

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=max_results)

        results = response.get("results", [])
        if not results:
            return "No results found."

        lines = []
        for r in results:
            lines.append(f"Title: {r.get('title', 'No title')}")
            lines.append(f"URL: {r.get('url', '')}")
            lines.append(f"Summary: {r.get('content', 'No content')}")
            lines.append("")

        return "\n".join(lines)[:4000]

    except Exception as e:
        return f"Search failed: {str(e)}"
```

**Update the `web_search` registration** in `init_executors()` (approx line 285-303). Change the description and parameters:

```python
    register(
        "web_search",
        _web_search,
        "Search the web using Tavily. Use when you need current information you don't have in memory.",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default 5)",
                },
            },
            "required": ["query"],
        },
    )
```

Note: the `region` parameter is gone. Tavily doesn't use it.

### File 2: `server.py`

**Update `_run_server_side_search`** to remove the `region` parameter. Find (approx line 300):

```python
def _run_server_side_search(query: str) -> str:
    """Run a web search server-side and return formatted results."""
    logger.info(f"Server-side search: {query}")
    result = executors.execute("web_search", {"query": query, "region": "us-en"})
    return result
```

Change to:

```python
def _run_server_side_search(query: str) -> str:
    """Run a web search server-side and return formatted results."""
    logger.info(f"Server-side search: {query}")
    result = executors.execute("web_search", {"query": query})
    return result
```

### File 3: Skills — replace duckduckgo-search with tavily-search

**Delete** the directory `skills/duckduckgo-search/` and its contents.

**Create** the directory `skills/tavily-search/` with a new `SKILL.md` file. The content for this file is provided separately as a drop-in file.

### File 4: `requirements.txt`

**Replace** `duckduckgo-search` with `tavily-python`:

```
fastapi
uvicorn
ollama
chromadb
cryptography
requests
pyyaml
tavily-python
```

### Install the new dependency

```bash
pip install tavily-python
```

## What NOT To Do

- Do NOT remove the `web_fetch` executor. It's separate from search.
- Do NOT change anything in the vault system — it already handles API keys correctly.
- Do NOT hardcode the API key anywhere. It comes from the vault.
- Do NOT touch any other executors.

## Verification

1. Make sure TAVILY_API_KEY is in the vault. Go to /settings in the browser and add it if it's not there.
2. Restart the server.
3. Startup banner should show skills loaded, including tavily-search. If the API key is in the vault, the skill status should be "ready".
4. Send a message that triggers search, e.g., "search for latest ChromaDB version"
5. Console should show `Server-side search: latest ChromaDB version`
6. Results should appear in the entity's response — from Tavily, not DuckDuckGo.
7. Check that results are coherent and relevant (Tavily typically gives much better results than DDG).

## Done When

Search works through Tavily, API key is in the vault (not hardcoded), old DuckDuckGo skill is removed, entity can search and summarize results.
