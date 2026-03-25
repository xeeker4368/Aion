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
CHAT_MODEL = "llama3.1:8b-aion"
CONSOLIDATION_MODEL = "qwen3:14b"
EMBED_MODEL = "nomic-embed-text"

# --- Context Window Budget ---
# llama3.1:8b default is 8192 tokens
# These are approximate — we estimate tokens as len(text) / 4
CONTEXT_WINDOW = 10240
SOUL_TOKEN_BUDGET = 500
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
# Final chunks: created when conversation ends
CHUNK_SIZE = 10       # messages per chunk
CHUNK_OVERLAP = 5     # overlap between final chunks

# --- Retrieval ---
RETRIEVAL_RESULTS = 5  # number of chunks to retrieve per search
