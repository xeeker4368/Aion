# Code Review Findings — 2026-04-01

Review of server.py, memory.py, db.py, config.py, config_manager.py, overnight.py, and static/index.html.

---

## CRITICAL

### 1. overnight.py:51 — `messages[-0:]` returns ALL messages, not empty list
When `msg_count % LIVE_CHUNK_INTERVAL == 0` (e.g. exactly 10, 20, 30 messages), `remaining = 0` and `messages[-0:]` is equivalent to `messages[0:]` — the entire list. This creates a duplicate chunk of all messages. The `if remaining > 0` guard on line 50 should prevent this, but if that guard is ever removed or bypassed, the bug surfaces. Worth verifying the guard is solid.

### 2. config.py:53-58 — `CONVERSATION_TOKEN_BUDGET` can go negative
`CONTEXT_WINDOW` is overridable via config.json, but `CONVERSATION_TOKEN_BUDGET = CONTEXT_WINDOW - SOUL_TOKEN_BUDGET - RETRIEVAL_TOKEN_BUDGET - RESPONSE_TOKEN_BUDGET` has no floor. Setting `CONTEXT_WINDOW` to a small value via config.json produces a negative budget that silently propagates.

### 3. server.py:559-567 — Potential duplicate tool execution
The two-pass LLM call pattern (first call may use tools, second call with tool results) doesn't guard against the model re-issuing the same tool calls on the second pass. If the model generates identical tool calls, they execute twice.

---

## HIGH

### 4. overnight.py:101-102 — KeyError if research returns `{skipped: True}`
`run_research()` can return `{"skipped": True}` without `tool_calls` or `stored_chars` keys. Lines 101-102 access these unconditionally. Same pattern exists for `run_journal()` (line 120, `experience_chars`) and `run_observer()` (line 138).

### 5. overnight.py:154-160 — Consolidation always runs, status lies
`consolidate_pending()` is called unconditionally on line 155, but status is set to `"skipped"` when `count == 0` on line 157. The log says "skipped" but the function ran.

### 6. db.py:193-224 — Dual-database write can leave inconsistent state
`save_message()` writes to both working and archive DBs. If `working_conn.commit()` succeeds but `archive_conn.commit()` fails, the databases diverge. No rollback coordination exists.

### 7. server.py:224-241 — Global `_active_conversation_id` has no lock
Concurrent async requests can race on the check-then-act pattern: Request A reads the ID, Request B clears it via `_end_active_conversation()`, Request A continues with stale state.

### 8. index.html:643 — `event.target` may be undefined
`filterActivity()` uses bare `event.target` without receiving `event` as a parameter. Works in inline `onclick` handlers in most browsers but is unreliable and will break in strict mode or programmatic calls.

---

## MEDIUM

### 9. config_manager.py:20-31 — Token budget constants not editable
`SOUL_TOKEN_BUDGET`, `RETRIEVAL_TOKEN_BUDGET`, and `RESPONSE_TOKEN_BUDGET` are hardcoded in config.py but not listed in `EDITABLE_SETTINGS`, so they can't be tuned through the config UI despite `_overrides.get()` being the pattern everywhere else.

### 10. db.py:22 — No timeout on `sqlite3.connect()`
If the DB is locked (e.g. overnight process holding a write lock), connections hang indefinitely. A reasonable `timeout=5` would prevent server stalls.

### 11. config.py:36-40 — Silent exception on corrupt config.json
`except Exception: pass` means a malformed config.json is silently ignored. The operator gets no indication their overrides aren't loading.

### 12. index.html:690-691 — No `res.ok` check before `.json()`
`toggleEntry()` calls `res.json()` without checking the response status. A 404/500 will either throw or render garbage content.

### 13. index.html:756-781 — Empty `catch(e) {}` blocks in config/secret operations
`updateConfig()`, `resetConfig()`, `addSecret()`, and `deleteSecret()` all silently swallow errors. The user gets no feedback when an API call fails.

### 14. index.html:759,765,780 — Unencoded URL path segments
Config keys and secret names are concatenated into fetch URLs without `encodeURIComponent()`. Keys containing `/`, `?`, or `#` will break the request.

### 15. index.html:510-516 — Fragile debug cache indexing
Debug data is keyed by counting `.msg.assistant` DOM elements. If the DOM structure changes or system messages are interspersed, indices drift and debug data attaches to wrong messages.

### 16. memory.py:252 — Search over-fetch heuristic may under-deliver
`fetch_count = min(n_results * 3, collection.count())` assumes 3x is enough to yield `n_results` unique conversations after dedup. If results cluster heavily in one conversation, fewer than `n_results` are returned.

### 17. index.html:664 — Unescaped summary text in overnight run display
`item[s+'_summary']` is injected into innerHTML without `esc()`. If summaries contain HTML-like content, this is an XSS vector.

---

## LOW

### 18. server.py:886 — Redundant datetime import inside `health_check()`
Already imported at module level.

### 19. db.py:181 — `is_conversation_ended()` doesn't distinguish "not found" from "still active"
Both return `False`. Callers can't tell if a conversation ID is invalid vs. ongoing.

### 20. memory.py:153-154 — No validation that `chunk_size > 0` or `overlap < chunk_size`
`chunk_text()` would loop forever or produce empty chunks with bad inputs.

### 21. index.html:515,528 — localStorage debug cache never cleaned up
Old conversation debug data accumulates indefinitely. No eviction strategy.

### 22. db.py:279 — `get_recent_conversations()` sorts by `started_at` with no index
Full table scan on every call. Minor now, grows with data.
