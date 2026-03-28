# CC Task 18: High-Priority Fixes

## Overview

Four targeted fixes. Each is small. Do them in order.

---

## Fix 1: Search Budget Counts Failures

**File:** `server.py`
**Problem:** `record_search()` fires even if the Tavily call errors. Wastes monthly budget on failures.

**Current code in `_run_server_side_search()`:**

```python
    logger.info(f"Server-side search: {query}")
    result = executors.execute("web_search", {"query": query})
    search_limiter.record_search()
```

**Change to:**

```python
    logger.info(f"Server-side search: {query}")
    result = executors.execute("web_search", {"query": query})

    # Only count successful searches against the budget
    if not result.startswith("Error:") and not result.startswith("Search failed:"):
        search_limiter.record_search()
    else:
        logger.warning(f"Search failed (not counted against budget): {result[:200]}")
```

---

## Fix 2: Foreign Keys Not Enforced

**File:** `db.py`
**Problem:** Foreign keys are defined in the schema but SQLite doesn't enforce them unless you enable the pragma per connection.

**Current code:**

```python
def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with row factory for dict-like access."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
```

**Change to:**

```python
def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with row factory for dict-like access."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
```

---

## Fix 3: Non-Atomic Dual Writes

**File:** `db.py`
**Problem:** `save_message()` writes to archive and working DB in separate connections. If the second write fails, the databases diverge. Principle 5 — data is sacred.

**Current code:**

```python
def save_message(conversation_id: str, role: str, content: str) -> dict:
    message_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Archive — append only, forever
    with _connect(ARCHIVE_DB) as conn:
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, conversation_id, role, content, now),
        )

    # Working store — same data, plus update conversation metadata
    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, conversation_id, role, content, now),
        )
        conn.execute(
            "UPDATE conversations SET message_count = message_count + 1 WHERE id = ?",
            (conversation_id,),
        )

    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "timestamp": now,
    }
```

**Change to:**

```python
def save_message(conversation_id: str, role: str, content: str) -> dict:
    message_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Write to both databases. If either fails, neither commits.
    archive_conn = _connect(ARCHIVE_DB)
    working_conn = _connect(WORKING_DB)

    try:
        archive_conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, conversation_id, role, content, now),
        )

        working_conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, conversation_id, role, content, now),
        )
        working_conn.execute(
            "UPDATE conversations SET message_count = message_count + 1 WHERE id = ?",
            (conversation_id,),
        )

        # Both succeeded — commit both
        archive_conn.commit()
        working_conn.commit()

    except Exception:
        archive_conn.rollback()
        working_conn.rollback()
        raise

    finally:
        archive_conn.close()
        working_conn.close()

    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "timestamp": now,
    }
```

**Note:** This is not true distributed atomicity — if the process crashes between the two commits, they can still diverge. But it handles the common failure case (bad data, constraint violation, disk full) cleanly. True atomicity across two SQLite databases would require a WAL coordinator, which is overkill for this system (Principle 2).

---

## Fix 4: Ingestion Confirmation Framing

**File:** `server.py`
**Problem:** The entity ignores ingestion confirmations and denies having the capability. The current framing ("A document was just saved to your memory") is too passive — the entity treats it as background noise and falls back to base training ("I was trained on...").

**Current code in `_ingest_url()` return string:**

Find the return statement that starts with:

```python
    return (
        f"Document saved to memory: \"{title}\" ({chunk_count} sections, "
        f"{len(content)} characters). You can now recall information from this "
        f"article in future conversations."
    )
```

**Change to:**

```python
    return (
        f"You just fetched and stored the web page at {url}. "
        f"It contained {len(content)} characters and was saved as {chunk_count} sections in your memory. "
        f"Tell the user the article has been saved and you can now recall it. "
        f"Do not say you already knew this or that you were trained on it — you just fetched it right now."
    )
```

**Also in `chat.py`**, find the ingestion result block:

```python
    if ingest_result:
        parts.append(
            f"\n\nA document was just saved to your memory: {ingest_result}"
        )
```

**Change to:**

```python
    if ingest_result:
        parts.append(
            f"\n\n{ingest_result}"
        )
```

The confirmation message is now self-contained and directive — no need for the wrapper text.

---

## Fix 5: Silent Memory Failures

**File:** `memory.py`
**Problem:** The `search()` function catches all exceptions and returns an empty list silently. Failures are invisible.

**Current code:**

```python
    try:
        results = collection.query(**query_params)
    except Exception:
        return []
```

**Change to:**

```python
    try:
        results = collection.query(**query_params)
    except Exception as e:
        import logging
        logging.getLogger("aion.memory").error(f"ChromaDB search failed: {e}")
        return []
```

---

## What NOT to Do

- Do NOT restructure the summaries table (#6). That's a separate task after these fixes.
- Do NOT add session management or request-level failure boundaries. Single-user system.
- Do NOT add SSRF protection or path sanitization. Single-user localhost.
- Do NOT change any other function signatures, return types, or behaviors.
- Do NOT remove dead code in this task. That's the next task.

## How to Verify

1. **Fix 1:** Temporarily set TAVILY_API_KEY to garbage in vault, trigger a search. Check `search_usage.json` — count should NOT increment. Fix the key back, search again — count SHOULD increment.
2. **Fix 2:** Check that foreign keys are enforced — try inserting a message with a non-existent conversation_id. Should fail.
3. **Fix 3:** Hard to test the failure path without simulating disk errors. Verify normal operation still works — send messages, check both databases have matching records.
4. **Fix 4:** Restart server, send "remember this article: https://en.wikipedia.org/wiki/Large_language_model". Entity should say something like "I've saved that article and can recall it now" instead of "I already know about that."
5. **Fix 5:** Hard to trigger in normal operation. Verify search still works normally. If ChromaDB is ever unavailable, the error will now appear in logs.
