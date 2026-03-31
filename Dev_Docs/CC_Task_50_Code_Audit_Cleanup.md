# CC Task 50: Code Audit Cleanup

**Priority:** After Task 49 (bug fixes), before go-live
**Risk:** Zero — dead code removal, cosmetic fixes, minor refactors
**Files to modify:** server.py, config.py, observer.py, journal.py, research.py, memory.py, executors.py, overnight.py, consolidation.py

---

## Part 1: Remove Dead Code

### Unused imports

**observer.py line 3** — remove:
```python
import json
```

**observer.py line 19** — change:
```python
from datetime import datetime, timezone, timedelta
```
to:
```python
from datetime import datetime, timezone
```

**journal.py line 15** — change:
```python
from datetime import datetime, timezone, timedelta
```
to:
```python
from datetime import datetime, timezone
```

**research.py line 20** — change:
```python
from config import CHAT_MODEL, CONTEXT_WINDOW
```
to:
```python
from config import CONTEXT_WINDOW
```

### Unused config values

**config.py line 46** — remove:
```python
SEARCH_FETCH_MAX_CHARS = 4000  # Max chars to include from fetched page
```

**config.py line 52-56** — remove:
```python
# --- Retrieval-Aware Search Gating ---
# If any chunk scores below this distance, memory is confident — skip web search.
# Lower distance = closer match. Cosine distance: 0.0 = identical, 2.0 = opposite.
# 0.35 is conservative — only strong matches suppress search.
MEMORY_CONFIDENCE_THRESHOLD = 0.35
```

### Unused function

**executors.py lines 39-41** — remove:
```python
def get_executor(name: str):
    """Get an executor by name."""
    return _executors.get(name)
```

(Note: `urlsafe_b64encode` removal from vault.py is already covered in Task 49.)

---

## Part 2: Fix Inconsistencies

### INCON-1: Misnamed debug field (server.py)

**Line 548** — change:
```python
            "definitions_count": len(tool_calls_made),
```
to:
```python
            "calls_count": len(tool_calls_made),
```

### INCON-2: retrieval_skipped incomplete (server.py)

The debug data should reflect ALL reasons retrieval was skipped, not just trivial messages.

**Line 456** — change:
```python
        "retrieval_skipped": _is_trivial_message(request.message),
```
to:
```python
        "retrieval_skipped": _is_trivial_message(request.message) or _targets_realtime_skill(request.message),
```

**Line 528** — change:
```python
            "retrieval_skipped": _is_trivial_message(request.message),
```
to:
```python
            "retrieval_skipped": _is_trivial_message(request.message) or _targets_realtime_skill(request.message),
```

### INCON-3: memory.init_memory() called inside loop (consolidation.py)

**Lines 107-111** — move the init outside the loop. Change the `summarize_documents` function:

**Current code (lines 91-108):**
```python
def summarize_documents():
    """Summarize any ingested documents that haven't been summarized yet."""
    pending = db.get_unsummarized_documents()

    if not pending:
        logger.info("No documents pending summarization.")
        return

    logger.info(f"Found {len(pending)} documents to summarize.")

    for doc in pending:
        doc_id = doc["id"]
        title = doc["title"]
        url = doc.get("url", "")

        # Get the chunks from ChromaDB for this document
        collection = None
        try:
            import memory
            memory.init_memory()
            collection = memory._get_collection()
```

**Replace with:**
```python
def summarize_documents():
    """Summarize any ingested documents that haven't been summarized yet."""
    pending = db.get_unsummarized_documents()

    if not pending:
        logger.info("No documents pending summarization.")
        return

    logger.info(f"Found {len(pending)} documents to summarize.")

    import memory
    memory.init_memory()

    for doc in pending:
        doc_id = doc["id"]
        title = doc["title"]
        url = doc.get("url", "")

        # Get the chunks from ChromaDB for this document
        collection = None
        try:
            collection = memory._get_collection()
```

---

## Part 3: Extract Shared Utility

`_format_timestamp` is duplicated identically in memory.py, observer.py, journal.py, and research.py.

### Create a new file: `utils.py`

```python
"""
Aion Utilities

Shared helper functions used across multiple modules.
"""

from datetime import datetime


def format_timestamp(iso_timestamp: str) -> str:
    """Convert ISO timestamp to human-readable format."""
    if not iso_timestamp:
        return "unknown time"
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        return dt.strftime("%B %d, %Y at %I:%M %p")
    except (ValueError, TypeError):
        return iso_timestamp
```

### Update all four files to use it:

**memory.py** — remove `_format_timestamp` function (lines 73-81), add import at top:
```python
from utils import format_timestamp
```
Change the one call site in `_messages_to_text` (line 68):
```python
        readable_time = format_timestamp(timestamp)
```

**observer.py** — remove `_format_timestamp` function (lines 143-152), add import at top:
```python
from utils import format_timestamp
```
Change the call site (line 82):
```python
            readable_time = format_timestamp(timestamp)
```

**journal.py** — remove `_format_timestamp` function (lines 167-175), add import at top:
```python
from utils import format_timestamp
```
Change the call site (line 150):
```python
                readable_time = format_timestamp(timestamp)
```

**research.py** — remove `_format_timestamp` function (lines 198-205), add import at top:
```python
from utils import format_timestamp
```
Change the call site (line 190):
```python
            readable_time = format_timestamp(timestamp)
```

---

## Part 4: Fix Private DB Access in overnight.py

**overnight.py lines 37-42** use `db._connect()` directly. Add a public function to db.py instead.

### Add to db.py (after the `end_conversation` function):

```python
def get_active_conversations() -> list[dict]:
    """Get all conversations that haven't been ended."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE ended_at IS NULL"
        ).fetchall()
    return [dict(row) for row in rows]
```

### Update overnight.py `_end_active_conversations` (lines 36-42):

**Current:**
```python
def _end_active_conversations():
    """End all active conversations. New day, fresh start."""
    with db._connect(db.WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE ended_at IS NULL"
        ).fetchall()

    active = [dict(row) for row in rows]
```

**Replace with:**
```python
def _end_active_conversations():
    """End all active conversations. New day, fresh start."""
    active = db.get_active_conversations()
```

---

## What NOT to Do

- Do NOT change any behavioral logic.
- Do NOT modify SOUL.md, chat.py, or db.py table schemas.
- Do NOT rename utils.py to anything else — keep it simple.
- Do NOT move any functions other than `_format_timestamp` into utils.py.
- Do NOT remove `get_executor` from executors.py if you find anything that references it — double-check with `grep -r "get_executor" *.py` first.

---

## Verification

```bash
# 1. Confirm all imports resolve
python -c "import db, memory, chat, executors, skills, vault, debug, search_limiter, consolidation, overnight, observer, journal, research, utils; print('All imports OK')"

# 2. Confirm removed config values are not referenced
grep -r "SEARCH_FETCH_MAX_CHARS" *.py
grep -r "MEMORY_CONFIDENCE_THRESHOLD" *.py
# Both should return nothing

# 3. Confirm get_executor is not called anywhere
grep -r "get_executor" *.py
# Should only show the definition if you forgot to remove it, or nothing

# 4. Confirm _format_timestamp is gone from all four files
grep -r "_format_timestamp" *.py
# Should return nothing — all replaced by utils.format_timestamp

# 5. Confirm overnight.py no longer uses db._connect
grep "_connect" overnight.py
# Should return nothing

# 6. Start the server and confirm it boots clean
python -c "import server; print('OK')"
```
