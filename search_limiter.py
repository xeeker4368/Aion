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
