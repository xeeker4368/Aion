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

# --- Ollama ---
OLLAMA_HOST = "http://localhost:11434"
CHAT_MODEL = "llama3.1:8b-aion"
CONSOLIDATION_MODEL = "qwen3:14b"
EMBED_MODEL = "nomic-embed-text"

# --- Context Window Budget ---
# llama3.1:8b default is 8192 tokens
# These are approximate — we estimate tokens as len(text) / 4
CONTEXT_WINDOW = 10240
SOUL_TOKEN_BUDGET = 663
RETRIEVAL_TOKEN_BUDGET = 1500
RESPONSE_TOKEN_BUDGET = 1000
CONVERSATION_TOKEN_BUDGET = (
    CONTEXT_WINDOW
    - SOUL_TOKEN_BUDGET
    - RETRIEVAL_TOKEN_BUDGET
    - RESPONSE_TOKEN_BUDGET
)

# --- Chunking ---
# Live chunks: created every N messages during active conversation
LIVE_CHUNK_INTERVAL = 10
# --- Retrieval ---
RETRIEVAL_RESULTS = 5  # number of chunks to retrieve per search

# --- Search Rate Limiting ---
SEARCH_MONTHLY_LIMIT = 1000  # Tavily free tier: 1000/month

# --- Document Ingestion ---
INGEST_CHUNK_SIZE = 1500       # chars per chunk (roughly 375 tokens)
INGEST_CHUNK_OVERLAP = 200     # chars overlap between chunks
