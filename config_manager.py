"""
Aion Config Manager

Reads and writes editable configuration to data/config.json.
Values in config.json override defaults from config.py.
The UI reads and writes through this module.
"""

import json
import logging
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger("aion.config_manager")

CONFIG_FILE = DATA_DIR / "config.json"

# Editable settings with their defaults and types
EDITABLE_SETTINGS = {
    "OLLAMA_HOST": {"default": "http://localhost:11434", "type": "string"},
    "CHAT_MODEL": {"default": "llama3.1:8b-aion", "type": "string"},
    "CONSOLIDATION_MODEL": {"default": "qwen3:14b", "type": "string"},
    "EMBED_MODEL": {"default": "nomic-embed-text", "type": "string"},
    "CONTEXT_WINDOW": {"default": 10240, "type": "integer"},
    "LIVE_CHUNK_INTERVAL": {"default": 10, "type": "integer"},
    "RETRIEVAL_RESULTS": {"default": 5, "type": "integer"},
    "RETRIEVAL_MAX_DISTANCE": {"default": 0.75, "type": "float"},
    "SEARCH_MONTHLY_LIMIT": {"default": 1000, "type": "integer"},
    "INGEST_CHUNK_SIZE": {"default": 3000, "type": "integer"},
    "INGEST_CHUNK_OVERLAP": {"default": 300, "type": "integer"},
    "OBSERVER_MIN_MESSAGES": {"default": 6, "type": "integer"},
}


def _load() -> dict:
    """Load config.json, return empty dict if missing."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save(data: dict):
    """Save config to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def get_all() -> dict:
    """Get all editable settings with current values."""
    overrides = _load()
    result = {}
    for key, meta in EDITABLE_SETTINGS.items():
        result[key] = {
            "value": overrides.get(key, meta["default"]),
            "default": meta["default"],
            "type": meta["type"],
            "modified": key in overrides,
        }
    return result


def get(key: str):
    """Get a single config value (override or default)."""
    overrides = _load()
    meta = EDITABLE_SETTINGS.get(key)
    if meta is None:
        return None
    return overrides.get(key, meta["default"])


def update(key: str, value) -> bool:
    """Update a single config value. Returns True if valid."""
    meta = EDITABLE_SETTINGS.get(key)
    if meta is None:
        return False

    # Type coercion
    if meta["type"] == "integer":
        try:
            value = int(value)
        except (ValueError, TypeError):
            return False
    elif meta["type"] == "string":
        value = str(value)
    elif meta["type"] == "float":
        try:
            value = float(value)
        except (ValueError, TypeError):
            return False

    overrides = _load()
    overrides[key] = value
    _save(overrides)
    logger.info(f"Config updated: {key} = {value}")
    return True


def reset(key: str) -> bool:
    """Reset a config value to its default."""
    overrides = _load()
    if key in overrides:
        del overrides[key]
        _save(overrides)
        logger.info(f"Config reset to default: {key}")
        return True
    return False
