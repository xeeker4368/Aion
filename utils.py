"""
Aion Utilities

Shared helper functions used across multiple modules.
"""

from datetime import datetime


def format_timestamp(iso_timestamp: str) -> str:
    """Convert ISO timestamp to human-readable format."""
    if not iso_timestamp:
        return "unknown time"
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        return dt.strftime("%B %d, %Y at %I:%M %p")
    except (ValueError, TypeError):
        return iso_timestamp
