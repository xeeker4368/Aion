# Aion Debug System — Implementation Spec

*For Claude Code · March 2026*

---

## What This Is

A visibility layer so Lyle can see exactly what the entity is receiving, what's in its context, and whether the configuration is correct. Two pieces: a startup banner and a per-request debug log.

**Why it matters:** Session 5 lost hours debugging tool calling. The root cause was a 2048 context window instead of 10240, and the system flooding every request with 30 facts + 5 summaries + 5 chunks + skill descriptions + tool definitions — including on greetings. Nobody could see what was happening because there was no visibility into what the model actually received. This fixes that.

**Guiding Principles in play:**
- **Principle 1** — Simple. One new file (`debug.py`), hooks in two existing files (`server.py`, `chat.py`). No database, no UI panel.
- **Principle 10** — Lyle can read the output and understand what the system is doing.
- **Principle 11** — When something breaks, the debug log shows exactly what the model received, token counts per section, and whether anything was truncated.

---

## File: `debug.py`

New file. All debug/visibility logic lives here.

### Constants

```
DEBUG_LOG_DIR = DATA_DIR / "logs"
DEBUG_LOG_FILE = DEBUG_LOG_DIR / "debug.log"
MAX_LOG_SIZE = 5 * 1024 * 1024  # 5MB per file
LOG_BACKUP_COUNT = 3            # Keep 3 rotated files
```

### Function: `init_debug()`

Called once during server startup (in `lifespan()`). Sets up:
- Creates `data/logs/` directory if it doesn't exist
- Configures a `RotatingFileHandler` on a logger named `"aion.debug"` pointing at `data/logs/debug.log`
- Rotation: 5MB per file, keep 3 backups (so max ~20MB of debug history)
- Log format: timestamp + the message, nothing else. No log level prefix needed — everything in this log is debug info.

### Function: `log_startup_banner()`

Called once during server startup, AFTER all systems are initialized but BEFORE "Aion ready." Prints to **both** console (stdout) AND the debug log file.

Outputs:

```
============================================================
AION STARTUP — {current ISO timestamp}
============================================================
Model:            {CHAT_MODEL}
Context Window:   {CONTEXT_WINDOW} tokens
Consolidation:    {CONSOLIDATION_MODEL}
Embedding:        {EMBED_MODEL}
Ollama Host:      {OLLAMA_HOST}

Token Budget:
  SOUL.md:        {SOUL_TOKEN_BUDGET} tokens
  Retrieval:      {RETRIEVAL_TOKEN_BUDGET} tokens
  Response:       {RESPONSE_TOKEN_BUDGET} tokens
  Conversation:   {CONVERSATION_TOKEN_BUDGET} tokens
  TOTAL:          {sum of all budgets} tokens
  {WARNING if sum != CONTEXT_WINDOW}

Paths:
  Archive DB:     {ARCHIVE_DB} ({file size or 'not found'})
  Working DB:     {WORKING_DB} ({file size or 'not found'})
  ChromaDB:       {CHROMA_DIR}
  SOUL.md:        {SOUL_PATH} ({file size or 'not found'})

Skills loaded:    {count} — {comma-separated names}
Vault keys:       {count} — {comma-separated key names}
ChromaDB docs:    {collection.count()} chunks indexed
============================================================
```

The WARNING line only appears if the budget math doesn't add up (i.e., `SOUL + RETRIEVAL + RESPONSE + CONVERSATION != CONTEXT_WINDOW`). This catches config drift.

**Implementation note:** Read all values directly from the `config` module at call time, not from cached variables. This ensures the banner reflects what the running system is actually using.

### Function: `log_request(request_data: dict)`

Called once per chat request, BEFORE sending to Ollama. Writes to both console (condensed) and debug log file (full detail).

**`request_data` dict structure** (assembled by the caller in `server.py`):

```python
{
    "timestamp": str,              # ISO timestamp
    "conversation_id": str,
    "message_number": int,         # Message count in this conversation
    "user_message": str,           # The raw user message

    # What was retrieved
    "facts_count": int,
    "facts_tokens": int,           # Estimated tokens for all facts loaded
    "chunks_count": int,
    "chunks_tokens": int,
    "summaries_count": int,
    "summaries_tokens": int,
    "skills_tokens": int,          # Skill descriptions tokens

    # Search activity
    "search_fired": bool,
    "search_type": str,            # "search", "confirm", "moltbook", or ""
    "search_query": str,           # What was searched
    "search_results_tokens": int,  # 0 if no search

    # The assembled prompt
    "soul_tokens": int,
    "system_prompt_total_tokens": int,
    "conversation_history_tokens": int,
    "conversation_messages_sent": int,  # How many messages after trimming
    "conversation_messages_total": int, # How many existed before trimming
    "messages_trimmed": int,            # How many were dropped

    # The bottom line
    "total_tokens": int,           # system_prompt + conversation_history
    "context_window": int,         # The configured limit
    "budget_exceeded": bool,       # total_tokens > context_window
    "headroom": int,               # context_window - total_tokens (negative if over)

    # Full system prompt (log file only, not console)
    "system_prompt_full": str,
}
```

**Console output** (condensed, one request = ~6 lines):

```
--- REQUEST #{message_number} | {timestamp} ---
User: {first 80 chars of user_message}...
Context: SOUL={soul_tokens} Facts={facts_count}({facts_tokens}t) Chunks={chunks_count}({chunks_tokens}t) Summaries={summaries_count}({summaries_tokens}t) Skills={skills_tokens}t
Search: {search_type}: "{search_query}" ({search_results_tokens}t)  [or "none"]
History: {conversation_messages_sent}/{conversation_messages_total} messages ({conversation_history_tokens}t) {" ⚠ TRIMMED {messages_trimmed}" if trimmed}
TOTAL: {total_tokens}/{context_window} tokens | Headroom: {headroom}t {"⚠ BUDGET EXCEEDED" if budget_exceeded}
```

**Debug log file output** (full detail): Same as console output PLUS the complete `system_prompt_full` text, separated by a clear marker:

```
--- FULL SYSTEM PROMPT START ---
{system_prompt_full}
--- FULL SYSTEM PROMPT END ---
```

This is the critical piece. When something goes wrong, you open the log, find the request, and read exactly what the model saw. No guessing.

### Function: `log_response(response_data: dict)`

Called once per chat request, AFTER receiving response from Ollama.

```python
{
    "timestamp": str,
    "response_tokens": int,        # Estimated tokens in response
    "response_preview": str,       # First 200 chars
    "total_round_trip_tokens": int, # request total + response
}
```

**Console output** (one line):

```
Response: {response_tokens}t | Round trip: {total_round_trip_tokens}/{context_window}t
```

**Debug log:** Same, plus full response text.

### Function: `estimate_tokens(text: str) -> int`

Centralized token estimation. Replaces the `_estimate_tokens` in `chat.py`. Same formula (`len(text) // 4`) but in one place so it's consistent everywhere.

---

## Changes to Existing Files

### `server.py`

**In `lifespan()` function:**

After all init calls and before `logger.info("Aion ready.")`, add:

```python
import debug
debug.init_debug()
debug.log_startup_banner()
```

**In `handle_chat()` function:**

After building the system prompt and trimming conversation (after current step 7, before step 8), assemble the `request_data` dict and call `debug.log_request()`.

After receiving the response from Ollama (after current step 8), call `debug.log_response()`.

The data assembly looks roughly like this (between current steps 7 and 8):

```python
# --- Debug logging ---
request_data = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "conversation_id": conversation_id,
    "message_number": msg_count,
    "user_message": request.message,
    "facts_count": len(facts),
    "facts_tokens": debug.estimate_tokens("\n".join(f.get("content", "") for f in facts)),
    "chunks_count": len(retrieved_chunks),
    "chunks_tokens": debug.estimate_tokens("\n".join(c.get("text", "") for c in retrieved_chunks)),
    "summaries_count": len(summaries),
    "summaries_tokens": debug.estimate_tokens("\n".join(s.get("content", "") for s in summaries)),
    "skills_tokens": debug.estimate_tokens(skill_desc),
    "search_fired": search_results is not None,
    "search_type": search_type if should_search else "",
    "search_query": query if should_search and search_type == "search" else (_pending_search_topic or ""),
    "search_results_tokens": debug.estimate_tokens(search_results) if search_results else 0,
    "soul_tokens": debug.estimate_tokens(chat.load_soul()),
    "system_prompt_total_tokens": debug.estimate_tokens(system_prompt),
    "conversation_history_tokens": sum(debug.estimate_tokens(m["content"]) for m in trimmed_messages),
    "conversation_messages_sent": len(trimmed_messages),
    "conversation_messages_total": len(conversation_messages),
    "messages_trimmed": len(conversation_messages) - len(trimmed_messages),
    "total_tokens": debug.estimate_tokens(system_prompt) + sum(debug.estimate_tokens(m["content"]) for m in trimmed_messages),
    "context_window": CONTEXT_WINDOW,
    "budget_exceeded": ...,  # total > context_window
    "headroom": ...,         # context_window - total
    "system_prompt_full": system_prompt,
}
debug.log_request(request_data)
```

Import `CONTEXT_WINDOW` from config at the top of server.py (it's not currently imported).

### `chat.py`

**Replace `_estimate_tokens()`** with an import from debug:

```python
from debug import estimate_tokens as _estimate_tokens
```

Or just import `debug` and call `debug.estimate_tokens()` everywhere. Either works. The point is one function, one place.

No other changes to chat.py.

---

## What NOT To Build

- **No database table for debug logs.** Files are simpler, rotatable, and greppable.
- **No web UI panel.** The console and log file are sufficient. If a UI becomes useful later, it reads the same log file. But don't build it now.
- **No separate config file.** Config stays in `config.py` as Python constants. The startup banner makes the active values visible. If config persistence becomes a problem (values reverting on code updates), that's a separate task — possibly a `config.local.py` override pattern. But don't solve that now.
- **No metrics, no timers, no performance tracking.** This is visibility into what the model sees, not a monitoring system.

---

## Verification

After implementation, verify with this checklist:

1. **Start the server.** The startup banner prints to the terminal with all correct values. Context window shows 10240. Token budgets add up.
2. **Send a greeting** ("hey, what's up"). Console shows the request breakdown. Facts, chunks, summaries loaded should be reasonable for a greeting. Check that the system isn't loading 30 facts for "hey."
3. **Send a question that triggers search** ("search for tavily python package"). Console shows search fired, the query used, and the search results token cost.
4. **Open `data/logs/debug.log`.** Find the last two requests. The full system prompt is there for both. Read it and confirm it matches what you'd expect the model to see.
5. **Check for truncation.** If total tokens exceed context window, there should be a visible warning in both console and log.
6. **Restart the server** (simulating a code update). The startup banner prints again. Verify the config values haven't changed.

---

## File Placement

```
aion/
├── debug.py            # NEW — all debug/visibility logic
├── data/
│   └── logs/
│       └── debug.log   # NEW — rotating debug log (auto-created)
└── (everything else unchanged)
```

---

*Aion Debug System Spec · March 2026 · Xeeker & Claude*
