# Optimization Audit — Session 14

*Generated 2026-03-30*

---

## HIGH IMPACT

### 1. No persistent DB connections — db.py
Every query opens a new `sqlite3.connect()`, sets 2 PRAGMAs (`WAL`, `foreign_keys`), runs the query, closes. A single chat request triggers 4+ connection cycles. Fix: persistent module-level connections initialized once in `init_databases()`.

### 2. Two-pass LLM calling doubles latency — server.py
Every tool-using request makes two full inference calls. The first pass (no tools) is discarded if tool intent is detected. Alternative: always send tools but add system prompt guidance to prevent reflexive use.

### 3. Same transcripts fetched 3x during overnight — overnight.py + research/journal/observer
Each step independently queries `get_conversations_ended_since()` and `get_conversation_messages()` for every conversation. 3N+3 queries for N conversations. Fix: gather once in `run_overnight()` and pass to each step.

---

## MEDIUM IMPACT

### 4. Token estimates computed twice per request — server.py
`estimate_tokens()` for trimmed_messages, chunks, skills, and soul are computed in both `request_data` and `frontend_debug` dicts. Fix: compute once, store in locals.

### 5. `_is_trivial_message()` called 3x per request — server.py:409,488,529
Linear scan through ~50 patterns each time. Fix: compute once at the top and reuse the boolean.

### 6. Ollama client re-created per iteration in batch — consolidation.py, observer.py
New `ollama.Client()` on every conversation/document during overnight. Fix: create once per batch function.

### 7. `memory.init_memory()` re-called in summarize loop — consolidation.py:110
Reinitializes ChromaDB client and embedding function on every document iteration. Fix: remove, it's already initialized by the caller (`overnight.py`).

### 8. `get_conversation_messages()` double-fetch — server.py
Fetched inside `_maybe_create_live_chunk()` (line 191) and then again in `handle_chat()` (line 444). Fix: return/cache the messages list.

### 9. SQLite backup without backup API — backup.py
`shutil.copy2` on live databases can produce corrupt backups if the server is writing. Fix: use `sqlite3.connect().backup()` for consistent snapshots.

---

## LOW-MEDIUM IMPACT

### 10. `list.pop(0)` in trimming loop — chat.py:135
O(n^2) for long conversations due to list shifting. Fix: use `collections.deque.popleft()` (O(1)) or compute the start index and slice once.

### 11. search_limiter reads JSON file twice per search — search_limiter.py
`can_search()` reads from disk, then `record_search()` reads again and writes back. 2 file reads + 1 write per search. Fix: cache in memory, write-through on updates.

---

## LOW IMPACT

### 12. `import re` inside `_web_fetch()` — executors.py:163
Re-imported on every web fetch call. Fix: move to top-level imports.

### 13. `_format_timestamp()` duplicated in 4 files — memory.py, observer.py, journal.py, research.py
Exact same function defined four times. Fix: define once in a shared location and import.

### 14. New `TavilyClient` on every search call — executors.py:193
`TavilyClient(api_key=api_key)` instantiated per search. Fix: cache after first creation.

### 15. Lazy imports in `_store_document()` — executors.py:223
`uuid`, `db`, `memory` imported inside the function. These modules are always available. Fix: move to top-level imports.

---

## Summary by Impact

| Impact | Finding | File |
|--------|---------|------|
| High | No persistent DB connections; new connection per query | db.py |
| High | Two-pass LLM calling doubles latency for tool requests | server.py |
| High | Same transcripts fetched 3x during overnight cycle | overnight.py + batch scripts |
| Medium | Token estimates computed twice per request | server.py |
| Medium | `_is_trivial_message()` called 3x per request | server.py |
| Medium | Ollama client re-created per iteration in batch | consolidation.py, observer.py |
| Medium | `memory.init_memory()` re-called in summarize loop | consolidation.py:110 |
| Medium | `get_conversation_messages()` double-fetch in chat flow | server.py |
| Medium | SQLite backup without backup API (integrity risk) | backup.py |
| Low-Med | `list.pop(0)` in conversation trimming loop — O(n^2) | chat.py:135 |
| Low-Med | `search_limiter` does 2 file reads + 1 write per search | search_limiter.py |
| Low | `import re` inside `_web_fetch()` | executors.py:163 |
| Low | `_format_timestamp()` duplicated in 4 files | memory/observer/journal/research |
| Low | New `TavilyClient` on every search call | executors.py:193 |
| Low | Lazy imports of `uuid`, `db`, `memory` in `_store_document()` | executors.py:223 |
