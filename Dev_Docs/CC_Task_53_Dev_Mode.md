# CC Task 53: Dev Mode

**Priority:** Before go-live — protects production data during testing
**Risk:** Low — no changes to production code paths
**Files to modify:** config.py, server.py

---

## What This Does

Start the server with `--dev` and it runs against a copy of the production databases in `data/dev/`. All conversations, chunks, and observations go to the copy. Production data is untouched. When you're done testing, delete `data/dev/` and it's gone.

Usage:
```bash
python server.py --dev          # starts in dev mode
python server.py                # starts in production mode (unchanged)
```

---

## The Changes

### config.py

Replace the paths section (lines 1-16) with:

```python
"""
Aion Configuration

Every tunable setting in one place. Change values here,
not scattered across the codebase.
"""

import os
import sys
from pathlib import Path

# --- Dev Mode ---
DEV_MODE = "--dev" in sys.argv or os.environ.get("AION_DEV_MODE") == "1"

# --- Paths ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

# In dev mode, databases go to data/dev/ — production data is untouched.
# Vault, logs, and search limiter stay in data/ (shared).
if DEV_MODE:
    _DB_DIR = DATA_DIR / "dev"
else:
    _DB_DIR = DATA_DIR

ARCHIVE_DB = _DB_DIR / "archive.db"
WORKING_DB = _DB_DIR / "working.db"
CHROMA_DIR = str(_DB_DIR / "chromadb")
SOUL_PATH = BASE_DIR / "soul.md"
```

Everything after line 16 stays exactly the same.

### server.py — lifespan function

Add the dev mode setup at the beginning of the lifespan function, BEFORE `db.init_databases()`. Add the import at the top of the file with the other imports:

```python
from config import LIVE_CHUNK_INTERVAL, CONTEXT_WINDOW, DEV_MODE
```

Then in the lifespan function, add this block right after `logger.info("Initializing Aion...")` and before `db.init_databases()`:

```python
    # Dev mode: copy production databases if dev directory is fresh
    if DEV_MODE:
        from pathlib import Path
        import shutil
        import sqlite3

        dev_dir = Path(config.DATA_DIR) / "dev"
        prod_archive = Path(config.DATA_DIR) / "archive.db"
        prod_working = Path(config.DATA_DIR) / "working.db"
        prod_chroma = Path(config.DATA_DIR) / "chromadb"

        dev_dir.mkdir(parents=True, exist_ok=True)

        # Copy SQLite databases using backup API (safe for live DBs)
        if prod_archive.exists() and not (dev_dir / "archive.db").exists():
            src = sqlite3.connect(str(prod_archive))
            dst = sqlite3.connect(str(dev_dir / "archive.db"))
            src.backup(dst)
            dst.close()
            src.close()
            logger.info("DEV MODE: Copied archive.db to dev/")

        if prod_working.exists() and not (dev_dir / "working.db").exists():
            src = sqlite3.connect(str(prod_working))
            dst = sqlite3.connect(str(dev_dir / "working.db"))
            src.backup(dst)
            dst.close()
            src.close()
            logger.info("DEV MODE: Copied working.db to dev/")

        # Copy ChromaDB directory
        if prod_chroma.exists() and not (dev_dir / "chromadb").exists():
            shutil.copytree(str(prod_chroma), str(dev_dir / "chromadb"))
            logger.info("DEV MODE: Copied chromadb/ to dev/")

        logger.warning("=" * 50)
        logger.warning("  DEV MODE ACTIVE — using data/dev/")
        logger.warning("  Production data is NOT being modified.")
        logger.warning("  Delete data/dev/ to reset.")
        logger.warning("=" * 50)
```

### server.py — startup banner

Also update the `debug.log_startup_banner()` area. Add after `debug.log_startup_banner()`:

```python
    if DEV_MODE:
        logger.warning("Aion ready. (DEV MODE)")
    else:
        logger.info("Aion ready.")
```

And remove the existing `logger.info("Aion ready.")` line that's there now.

---

## What NOT to Do

- Do NOT change DATA_DIR itself — vault, logs, and search_limiter use it and should stay on production paths.
- Do NOT auto-create dev copies on every startup — only copy if the dev directory doesn't have the files yet. This lets you test across multiple restarts without resetting.
- Do NOT change any database logic, memory logic, or chat logic.
- Do NOT change the overnight cycle — overnight always runs against whatever config.py says, so `python overnight.py --dev` would work if you needed it, but normally you won't.
- Do NOT change backup.py — backups should always target production.

---

## Verification

1. Start in dev mode:
   ```bash
   python server.py --dev
   ```
2. Confirm the banner prints: `DEV MODE ACTIVE — using data/dev/`
3. Confirm `data/dev/` was created with copies of archive.db, working.db, and chromadb/
4. Send a test message. Confirm it works normally.
5. Check that the test message is in the dev database, NOT production:
   ```bash
   sqlite3 data/dev/working.db "SELECT content FROM messages ORDER BY timestamp DESC LIMIT 1;"
   sqlite3 data/working.db "SELECT content FROM messages ORDER BY timestamp DESC LIMIT 1;"
   ```
   The test message should appear in dev, not in production.
6. Stop the server. Start normally (no `--dev` flag). Confirm it uses production data and the dev test message is not visible.
7. To reset dev: `rm -rf data/dev/` — next `--dev` start copies fresh from production.
