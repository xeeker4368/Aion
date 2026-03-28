"""
Aion Configuration

Every tunable setting in one place. Change values here,
not scattered across the codebase.
"""

from pathlib import Path

# --- Paths ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
ARCHIVE_DB = DATA_DIR / "archive.db"
WORKING_DB = DATA_DIR / "working.db"
CHROMA_DIR = str(DATA_DIR / "chromadb")
SOUL_PATH = BASE_DIR / "soul.md"

# --- Ollama ---
OLLAMA_HOST = "http://localhost:11434"
#CHAT_MODEL = "hermes3:latest"
CHAT_MODEL = "hermes3:8b-aion"
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
SEARCH_FETCH_MAX_CHARS = 4000  # Max chars to include from fetched page

# --- Document Ingestion ---
INGEST_CHUNK_SIZE = 1500       # chars per chunk (roughly 375 tokens)
INGEST_CHUNK_OVERLAP = 200     # chars overlap between chunks

# --- Retrieval-Aware Search Gating ---
# If any chunk scores below this distance, memory is confident — skip web search.
# Lower distance = closer match. Cosine distance: 0.0 = identical, 2.0 = opposite.
# 0.35 is conservative — only strong matches suppress search.
MEMORY_CONFIDENCE_THRESHOLD = 0.35
