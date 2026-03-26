# CC Task 7: Search Rate Limiting

## What This Is

Limit how many Tavily API calls can be made per month. The free tier allows 1000/month. Add a configurable monthly cap with a counter that resets automatically when the month changes.

## Changes

### File 1: `config.py`

Add at the bottom, after the retrieval settings:

```python
# --- Search Rate Limiting ---
SEARCH_MONTHLY_LIMIT = 1000  # Tavily free tier: 1000/month
```

### File 2: Create new file `search_limiter.py`

Create this file in the project root:

```python
"""
Aion Search Rate Limiter

Tracks monthly search API calls and enforces a configurable limit.
Counter stored in data/search_usage.json, resets when the month changes.
"""

import json
import logging
from datetime import datetime, timezone
from config import DATA_DIR, SEARCH_MONTHLY_LIMIT

logger = logging.getLogger("aion.search_limiter")

USAGE_FILE = DATA_DIR / "search_usage.json"


def _load_usage() -> dict:
    """Load current usage data, resetting if month has changed."""
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")

    if USAGE_FILE.exists():
        try:
            data = json.loads(USAGE_FILE.read_text())
            if data.get("month") == current_month:
                return data
            else:
                logger.info(
                    f"New month ({current_month}). "
                    f"Resetting search counter (was {data.get('count', 0)})."
                )
        except (json.JSONDecodeError, KeyError):
            pass

    # Fresh month or first run
    return {"month": current_month, "count": 0}


def _save_usage(data: dict):
    """Save usage data to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(data))


def can_search() -> bool:
    """Check if we're under the monthly limit."""
    data = _load_usage()
    return data["count"] < SEARCH_MONTHLY_LIMIT


def record_search():
    """Record a search call. Call this after a successful search."""
    data = _load_usage()
    data["count"] += 1
    _save_usage(data)

    remaining = SEARCH_MONTHLY_LIMIT - data["count"]
    if remaining <= 100:
        logger.warning(f"Search budget: {remaining} searches remaining this month")
    elif remaining % 100 == 0:
        logger.info(f"Search budget: {remaining} searches remaining this month")


def get_usage() -> dict:
    """Get current usage stats."""
    data = _load_usage()
    return {
        "month": data["month"],
        "used": data["count"],
        "limit": SEARCH_MONTHLY_LIMIT,
        "remaining": SEARCH_MONTHLY_LIMIT - data["count"],
    }
```

### File 3: `server.py`

**Add the import** near the top with the other imports:

```python
import search_limiter
```

**Update `_run_server_side_search`** to check the limit before searching and record after. Change:

```python
def _run_server_side_search(query: str) -> str:
    """Run a web search server-side and return formatted results."""
    logger.info(f"Server-side search: {query}")
    result = executors.execute("web_search", {"query": query})
    return result
```

To:

```python
def _run_server_side_search(query: str) -> str:
    """Run a web search server-side and return formatted results."""
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
    return result
```

**Add a search usage API endpoint** so you can check usage from the settings page or browser. Add this near the other API endpoints:

```python
@app.get("/api/search/usage")
async def search_usage():
    """Get current search API usage stats."""
    return search_limiter.get_usage()
```

## What NOT To Do

- Do NOT touch executors.py or the Tavily implementation.
- Do NOT add the counter to the vault — it's not a secret, it's operational data.
- Do NOT make this complicated. File-based JSON is correct for a single counter.

## Verification

1. Restart the server. No errors.
2. Hit `http://localhost:8000/api/search/usage` in the browser. Should return JSON with month, used, limit, remaining.
3. Trigger a search in the chat (e.g., "search for Python 3.13 release date").
4. Hit the usage endpoint again. `used` should be 1, `remaining` should be 999.
5. Optionally: temporarily set `SEARCH_MONTHLY_LIMIT = 1` in config.py, restart, trigger two searches. The second should be blocked with a log warning. Then set it back to 1000.

## Done When

Searches are counted, the limit is enforced, usage is visible via the API endpoint, and the counter resets automatically on the first of each month.
