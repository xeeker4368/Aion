# Code Audit — Session 14

*Generated 2026-03-30*

---

## BUGS (will break at runtime)

### BUG-1: `server.py:424` — dist format crash on missing distance
```python
dist = chunk.get("distance", "?")
logger.info(f"  Chunk {i}: [{dist:.4f}] ...")
```
If a chunk has no distance key, `dist` becomes the string `"?"` and `:.4f` raises `ValueError`.

### BUG-2: `backup.py` — Copying live SQLite databases with shutil
Uses `shutil.copy2` on SQLite databases while the server may be running. If the server is writing during backup (cron runs while server is up), the backup can be corrupt. Should use SQLite's `.backup()` API.

### BUG-3: `vault.py:98-100` — Repeated init on decryption failure
If `init_secrets()` fails (decryption error), `_secrets` stays `{}`, and every subsequent `get()` call re-triggers `init_secrets()` because `not _secrets` is True and the file exists. Logs the error on every secret lookup.

---

## INCONSISTENCIES

### INCON-1: `server.py:548` — Misnamed debug field
`definitions_count` is set to `len(tool_calls_made)` but the field name says "definitions_count". Misleading debug data.

### INCON-2: `server.py:456,528` — retrieval_skipped incomplete
`retrieval_skipped` only checks `_is_trivial_message`, doesn't account for `_targets_realtime_skill`. Debug shows `retrieval_skipped: false` when retrieval was actually skipped for realtime reasons.

### INCON-3: `consolidation.py:109-111` — memory.init_memory() called in loop
`memory.init_memory()` called inside the document summarization loop, reinitializing ChromaDB client on every iteration.

### INCON-4: `research.py:107` — Over-broad error detection
Error detection matches `'error'` and `'http 4'` too broadly. Text like "no errors found" in a result would trigger the guard, discarding valid research.

---

## DEAD CODE

- `vault.py:21` — `urlsafe_b64encode` imported, never used
- `config.py:46` — `SEARCH_FETCH_MAX_CHARS` defined, never referenced
- `config.py:56` — `MEMORY_CONFIDENCE_THRESHOLD` defined, never referenced
- `observer.py:3` — `json` imported, never used
- `observer.py:19` — `timedelta` imported, never used
- `journal.py:15` — `timedelta` imported, never used
- `research.py:20` — `CHAT_MODEL` imported, never used
- `executors.py:40-42` — `get_executor()` defined, never called

---

## NOTES

- `server.py` — All endpoints are `async def` but call synchronous functions (db, Ollama). This blocks the event loop — FastAPI won't threadpool them because they're async.
- `overnight.py:37` — Accesses private `db._connect()` directly.
- `_format_timestamp` is duplicated identically in 4 files (memory.py, observer.py, journal.py, research.py).
- `search_limiter.py` — No file locking on `search_usage.json`, concurrent writes can lose updates.
