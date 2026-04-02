"""
Aion Configuration

Every tunable setting in one place. Change values here,
not scattered across the codebase.
"""

import os
import sys
from pathlib import Path

# --- Dev Mode ---
DEV_MODE = "--dev" in sys.argv or os.environ.get("AION_DEV_MODE") == "1"

# --- Paths ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

# In dev mode, databases go to data/dev/ — production data is untouched.
# Vault, logs, and search limiter stay in data/ (shared).
if DEV_MODE:
    _DB_DIR = DATA_DIR / "dev"
else:
    _DB_DIR = DATA_DIR

ARCHIVE_DB = _DB_DIR / "archive.db"
WORKING_DB = _DB_DIR / "working.db"
CHROMA_DIR = str(_DB_DIR / "chromadb")
SOUL_PATH = BASE_DIR / "soul.md"

# --- Load overrides from config.json ---
import json as _json

_CONFIG_FILE = DATA_DIR / "config.json"
_overrides = {}
if _CONFIG_FILE.exists():
    try:
        _overrides = _json.loads(_CONFIG_FILE.read_text())
    except Exception as _e:
        import sys
        print(
            f"WARNING: Failed to parse {_CONFIG_FILE}: {_e}. Using defaults.",
            file=sys.stderr,
        )

# --- Ollama ---
OLLAMA_HOST = _overrides.get("OLLAMA_HOST", "http://localhost:11434")
CHAT_MODEL = _overrides.get("CHAT_MODEL", "llama3.1:8b-aion")
CONSOLIDATION_MODEL = _overrides.get("CONSOLIDATION_MODEL", "qwen3:14b")
EMBED_MODEL = _overrides.get("EMBED_MODEL", "nomic-embed-text")

# --- Context Window Budget ---
CONTEXT_WINDOW = _overrides.get("CONTEXT_WINDOW", 10240)
SOUL_TOKEN_BUDGET = 663
RETRIEVAL_TOKEN_BUDGET = 1500
RESPONSE_TOKEN_BUDGET = 1000
CONVERSATION_TOKEN_BUDGET = max(
    0,
    CONTEXT_WINDOW
    - SOUL_TOKEN_BUDGET
    - RETRIEVAL_TOKEN_BUDGET
    - RESPONSE_TOKEN_BUDGET,
)
if CONVERSATION_TOKEN_BUDGET == 0:
    import sys
    print(
        f"WARNING: CONTEXT_WINDOW ({CONTEXT_WINDOW}) is too small for the token budgets. "
        f"No room for conversation history. Increase CONTEXT_WINDOW or reset config.",
        file=sys.stderr,
    )

# --- Chunking ---
LIVE_CHUNK_INTERVAL = _overrides.get("LIVE_CHUNK_INTERVAL", 10)

# --- Retrieval ---
RETRIEVAL_RESULTS = _overrides.get("RETRIEVAL_RESULTS", 5)
RETRIEVAL_MAX_DISTANCE = _overrides.get("RETRIEVAL_MAX_DISTANCE", 0.75)

# --- Search Rate Limiting ---
SEARCH_MONTHLY_LIMIT = _overrides.get("SEARCH_MONTHLY_LIMIT", 1000)

# --- Document Ingestion ---
INGEST_CHUNK_SIZE = _overrides.get("INGEST_CHUNK_SIZE", 3000)
INGEST_CHUNK_OVERLAP = _overrides.get("INGEST_CHUNK_OVERLAP", 300)

# --- Observer ---
OBSERVER_MIN_MESSAGES = _overrides.get("OBSERVER_MIN_MESSAGES", 6)
