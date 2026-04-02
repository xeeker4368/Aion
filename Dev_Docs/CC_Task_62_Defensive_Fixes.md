# CC Task 62 — Defensive Fixes from Code Review

Read this spec. Make exactly these changes. Nothing else.

## Overview

Four defensive fixes identified during external code review. No architecture changes. No behavioral changes.

---

## Change 1: config.py — Floor clamp on CONVERSATION_TOKEN_BUDGET

`CONVERSATION_TOKEN_BUDGET` is calculated as `CONTEXT_WINDOW - SOUL - RETRIEVAL - RESPONSE`. If someone sets a small `CONTEXT_WINDOW` via the config UI, this goes negative. The trim function in chat.py would strip all conversation history, and the entity would respond with zero context.

Replace lines 53-58:

```python
CONVERSATION_TOKEN_BUDGET = (
    CONTEXT_WINDOW
    - SOUL_TOKEN_BUDGET
    - RETRIEVAL_TOKEN_BUDGET
    - RESPONSE_TOKEN_BUDGET
)
```

With:

```python
CONVERSATION_TOKEN_BUDGET = max(
    0,
    CONTEXT_WINDOW
    - SOUL_TOKEN_BUDGET
    - RETRIEVAL_TOKEN_BUDGET
    - RESPONSE_TOKEN_BUDGET,
)
if CONVERSATION_TOKEN_BUDGET == 0:
    import sys
    print(
        f"WARNING: CONTEXT_WINDOW ({CONTEXT_WINDOW}) is too small for the token budgets. "
        f"No room for conversation history. Increase CONTEXT_WINDOW or reset config.",
        file=sys.stderr,
    )
```

---

## Change 2: config.py — Warn on corrupt config.json

Replace lines 36-40:

```python
if _CONFIG_FILE.exists():
    try:
        _overrides = _json.loads(_CONFIG_FILE.read_text())
    except Exception:
        pass
```

With:

```python
if _CONFIG_FILE.exists():
    try:
        _overrides = _json.loads(_CONFIG_FILE.read_text())
    except Exception as _e:
        import sys
        print(
            f"WARNING: Failed to parse {_CONFIG_FILE}: {_e}. Using defaults.",
            file=sys.stderr,
        )
```

Note: config.py runs at import time before logging is initialized, so `print` to stderr is the only reliable option here.

---

## Change 3: db.py — Add timeout to sqlite3.connect()

The overnight cron and the server can contend on the database. WAL mode helps but doesn't eliminate all blocking. Without a timeout, a locked database hangs the connection indefinitely.

Replace line 22:

```python
    conn = sqlite3.connect(str(db_path))
```

With:

```python
    conn = sqlite3.connect(str(db_path), timeout=5)
```

---

## Change 4: server.py — Clean up redundant import in health_check

Line 881 has `from datetime import datetime, timezone, timedelta` inside `health_check()`. The module-level import at line 29 already has `datetime` and `timezone` but is missing `timedelta`.

**Step A:** Add `timedelta` to the module-level import. Replace line 29:

```python
from datetime import datetime, timezone
```

With:

```python
from datetime import datetime, timezone, timedelta
```

**Step B:** Delete line 881:

```python
        from datetime import datetime, timezone, timedelta
```

---

## What NOT to Do

- Do NOT change any other code in these files.
- Do NOT add logging imports to config.py (logging isn't initialized at import time).
- Do NOT change the token budget values themselves — only add the floor clamp.
- Do NOT change the SQLite timeout to anything other than 5 seconds.

## Verification

1. **Budget clamp**: `python3 -c "import json; open('data/config.json','w').write(json.dumps({'CONTEXT_WINDOW': 1000})); from config import CONVERSATION_TOKEN_BUDGET; print(CONVERSATION_TOKEN_BUDGET)"` — should print `0` and a WARNING to stderr. Then delete the test override: `python3 -c "import json; open('data/config.json','w').write(json.dumps({}))"` (or delete config.json).
2. **Corrupt config warning**: `echo "not json" > data/config.json && python3 -c "import config"` — should print a WARNING to stderr. Then clean up: `rm data/config.json`.
3. **SQLite timeout**: `python3 -c "import db; print(db._connect(db.WORKING_DB).execute('PRAGMA busy_timeout').fetchone()[0])"` — should print `5000` (SQLite reports timeout in milliseconds).
4. **Redundant import**: `grep -n "from datetime" server.py` — should show only one line (the module-level import) with `datetime, timezone, timedelta`.
