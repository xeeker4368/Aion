"""
Aion Debug System

Visibility layer: startup banner + per-request debug logging.
Shows exactly what the model receives, token counts per section,
and whether anything was truncated.
"""

import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from config import DATA_DIR

DEBUG_LOG_DIR = DATA_DIR / "logs"
DEBUG_LOG_FILE = DEBUG_LOG_DIR / "debug.log"
MAX_LOG_SIZE = 5 * 1024 * 1024   # 5MB per file
LOG_BACKUP_COUNT = 3              # Keep 3 rotated files

_debug_logger = None


def _get_logger():
    global _debug_logger
    if _debug_logger is None:
        init_debug()
    return _debug_logger


def estimate_tokens(text: str) -> int:
    """Rough token estimate. 1 token ≈ 4 characters for English text."""
    return len(text) // 4


def init_debug():
    """Set up the debug log directory and rotating file handler."""
    global _debug_logger

    DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)

    _debug_logger = logging.getLogger("aion.debug")
    _debug_logger.setLevel(logging.DEBUG)

    # Avoid adding duplicate handlers on re-init
    if not _debug_logger.handlers:
        handler = RotatingFileHandler(
            str(DEBUG_LOG_FILE),
            maxBytes=MAX_LOG_SIZE,
            backupCount=LOG_BACKUP_COUNT,
        )
        handler.setFormatter(logging.Formatter("%(asctime)s\t%(message)s"))
        _debug_logger.addHandler(handler)


def _file_size_str(path) -> str:
    """Return human-readable file size or 'not found'."""
    try:
        size = os.path.getsize(path)
        if size < 1024:
            return f"{size}B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        else:
            return f"{size / (1024 * 1024):.1f}MB"
    except OSError:
        return "not found"


def log_startup_banner():
    """Print startup banner to both console and debug log."""
    import config
    import skills
    import vault
    import memory

    now = datetime.now(timezone.utc).isoformat()

    soul_budget = config.SOUL_TOKEN_BUDGET
    retrieval_budget = config.RETRIEVAL_TOKEN_BUDGET
    response_budget = config.RESPONSE_TOKEN_BUDGET
    conversation_budget = config.CONVERSATION_TOKEN_BUDGET
    total_budget = soul_budget + retrieval_budget + response_budget + conversation_budget

    budget_warning = ""
    if total_budget != config.CONTEXT_WINDOW:
        budget_warning = f"  ⚠ WARNING: Budget total ({total_budget}) != Context Window ({config.CONTEXT_WINDOW})"

    # Skills
    skill_list = skills.list_skills()
    skill_names = ", ".join(s["name"] for s in skill_list) if skill_list else "none"

    # Vault
    vault_keys = vault.list_keys()
    vault_names = ", ".join(vault_keys) if vault_keys else "none"

    # ChromaDB
    try:
        collection = memory._get_collection()
        chroma_count = collection.count()
    except Exception:
        chroma_count = "error"

    banner = f"""============================================================
AION STARTUP — {now}
============================================================
Model:            {config.CHAT_MODEL}
Context Window:   {config.CONTEXT_WINDOW} tokens
Consolidation:    {config.CONSOLIDATION_MODEL}
Embedding:        {config.EMBED_MODEL}
Ollama Host:      {config.OLLAMA_HOST}

Token Budget:
  SOUL.md:        {soul_budget} tokens
  Retrieval:      {retrieval_budget} tokens
  Response:       {response_budget} tokens
  Conversation:   {conversation_budget} tokens
  TOTAL:          {total_budget} tokens
{budget_warning}
Paths:
  Archive DB:     {config.ARCHIVE_DB} ({_file_size_str(config.ARCHIVE_DB)})
  Working DB:     {config.WORKING_DB} ({_file_size_str(config.WORKING_DB)})
  ChromaDB:       {config.CHROMA_DIR}
  SOUL.md:        {config.SOUL_PATH} ({_file_size_str(config.SOUL_PATH)})

Skills loaded:    {len(skill_list)} — {skill_names}
Vault keys:       {len(vault_keys)} — {vault_names}
ChromaDB docs:    {chroma_count} chunks indexed
============================================================"""

    # Print to console
    print(banner)

    # Write to debug log
    logger = _get_logger()
    for line in banner.split("\n"):
        logger.debug(line)


def log_request(request_data: dict):
    """Log request details to console (condensed) and debug log (full)."""
    d = request_data
    logger = _get_logger()

    # User message preview
    user_preview = d["user_message"][:80]
    if len(d["user_message"]) > 80:
        user_preview += "..."

    # Search line
    if d["search_fired"]:
        search_line = f'{d["search_type"]}: "{d["search_query"]}" ({d["search_results_tokens"]}t)'
    else:
        search_line = "none"

    # Trimmed warning
    trim_warning = ""
    if d["messages_trimmed"] > 0:
        trim_warning = f" ⚠ TRIMMED {d['messages_trimmed']}"

    # Budget warning
    budget_warning = " ⚠ BUDGET EXCEEDED" if d["budget_exceeded"] else ""

    from datetime import datetime
    readable_time = datetime.fromisoformat(d["timestamp"]).strftime("%I:%M:%S %p")
    console_lines = [
        f'--- REQUEST #{d["message_number"]} | {readable_time} ---',
        f"User: {user_preview}",
        f'Context: SOUL={d["soul_tokens"]} Chunks={d["chunks_count"]}({d["chunks_tokens"]}t) Summaries={d["summaries_count"]}({d["summaries_tokens"]}t) Skills={d["skills_tokens"]}t{" [RETRIEVAL SKIPPED]" if d.get("retrieval_skipped") else ""}',
        f"Search: {search_line}",
        f'History: {d["conversation_messages_sent"]}/{d["conversation_messages_total"]} messages ({d["conversation_history_tokens"]}t){trim_warning}',
        f'TOTAL: {d["total_tokens"]}/{d["context_window"]} tokens | Headroom: {d["headroom"]}t{budget_warning}',
    ]

    # Console output
    for line in console_lines:
        print(line)

    # Debug log: same lines plus full system prompt
    for line in console_lines:
        logger.debug(line)

    logger.debug("--- FULL SYSTEM PROMPT START ---")
    for line in d["system_prompt_full"].split("\n"):
        logger.debug(line)
    logger.debug("--- FULL SYSTEM PROMPT END ---")


def log_response(response_data: dict):
    """Log response details to console (one line) and debug log (full)."""
    d = response_data
    logger = _get_logger()

    console_line = f'Response: {d["response_tokens"]}t | Round trip: {d["total_round_trip_tokens"]}/{d.get("context_window", "")}t'

    print(console_line)
    logger.debug(console_line)

    # Full response in log only
    if "response_full" in d:
        logger.debug("--- FULL RESPONSE START ---")
        for line in d["response_full"].split("\n"):
            logger.debug(line)
        logger.debug("--- FULL RESPONSE END ---")
