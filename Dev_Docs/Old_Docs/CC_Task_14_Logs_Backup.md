# CC Task 14: Daily Log Rotation + Automated Backup

## What This Is

Two infrastructure improvements:
1. Debug logs split by day instead of by size
2. Automated daily backup of all databases

## Changes

### File 1: `debug.py`

**Switch from size-based to daily log rotation.** Change the import and handler setup.

Replace:
```python
from logging.handlers import RotatingFileHandler
```
With:
```python
from logging.handlers import TimedRotatingFileHandler
```

Replace:
```python
MAX_LOG_SIZE = 5 * 1024 * 1024   # 5MB per file
LOG_BACKUP_COUNT = 3              # Keep 3 rotated files
```
With:
```python
LOG_BACKUP_DAYS = 30              # Keep 30 days of logs
```

Replace the handler creation in `init_debug()`:
```python
        handler = RotatingFileHandler(
            str(DEBUG_LOG_FILE),
            maxBytes=MAX_LOG_SIZE,
            backupCount=LOG_BACKUP_COUNT,
        )
        handler.setFormatter(logging.Formatter("%(asctime)s\t%(message)s"))
```
With:
```python
        handler = TimedRotatingFileHandler(
            str(DEBUG_LOG_FILE),
            when="midnight",
            backupCount=LOG_BACKUP_DAYS,
        )
        handler.suffix = "%Y-%m-%d"
        handler.setFormatter(logging.Formatter("%(asctime)s\t%(message)s"))
```

This creates files like `debug.log.2026-03-27`, `debug.log.2026-03-26`, etc. The current day is always `debug.log`. After 30 days, old files are deleted automatically.

### File 2: Create `backup.py` in the project root

```python
"""
Aion Backup

Creates a timestamped backup of all databases and ChromaDB.
Run daily via cron, or manually when needed.

Usage:
    python backup.py           # backup to data/backups/
    python backup.py /path     # backup to custom location
"""

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import ARCHIVE_DB, WORKING_DB, CHROMA_DIR, DATA_DIR

# Default backup location
DEFAULT_BACKUP_DIR = DATA_DIR / "backups"


def run_backup(backup_root: Path = None):
    if backup_root is None:
        backup_root = DEFAULT_BACKUP_DIR

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    backup_dir = backup_root / f"aion_backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    print(f"Backing up to {backup_dir}...")

    # Archive DB
    if ARCHIVE_DB.exists():
        shutil.copy2(str(ARCHIVE_DB), str(backup_dir / "archive.db"))
        size = ARCHIVE_DB.stat().st_size / 1024
        print(f"  archive.db: {size:.1f} KB")

    # Working DB
    if WORKING_DB.exists():
        shutil.copy2(str(WORKING_DB), str(backup_dir / "working.db"))
        size = WORKING_DB.stat().st_size / 1024
        print(f"  working.db: {size:.1f} KB")

    # ChromaDB directory
    chroma_path = Path(CHROMA_DIR)
    if chroma_path.exists():
        shutil.copytree(str(chroma_path), str(backup_dir / "chromadb"))
        total = sum(f.stat().st_size for f in chroma_path.rglob("*") if f.is_file())
        print(f"  chromadb/: {total / 1024:.1f} KB")

    # Vault secrets
    secrets_file = DATA_DIR / "secrets.enc"
    if secrets_file.exists():
        shutil.copy2(str(secrets_file), str(backup_dir / "secrets.enc"))
        print(f"  secrets.enc: copied")

    master_key = DATA_DIR / ".master_key"
    if master_key.exists():
        shutil.copy2(str(master_key), str(backup_dir / ".master_key"))
        print(f"  .master_key: copied")

    # Clean up old backups — keep last 7
    all_backups = sorted(backup_root.glob("aion_backup_*"))
    if len(all_backups) > 7:
        for old in all_backups[:-7]:
            shutil.rmtree(str(old))
            print(f"  Removed old backup: {old.name}")

    print(f"\nBackup complete: {backup_dir}")
    return str(backup_dir)


if __name__ == "__main__":
    custom_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    run_backup(custom_path)
```

### File 3: Create `setup_cron.sh` in the project root

```bash
#!/bin/bash
# Sets up daily backup cron job for Aion
# Run once: bash setup_cron.sh

AION_DIR="$HOME/aion"
PYTHON="$AION_DIR/aion/bin/python"

# Check if venv python exists
if [ ! -f "$PYTHON" ]; then
    echo "Error: Python not found at $PYTHON"
    exit 1
fi

# Add cron job: run backup daily at 1:00 AM
CRON_CMD="0 1 * * * cd $AION_DIR && $PYTHON backup.py >> $AION_DIR/data/logs/backup.log 2>&1"

# Check if already installed
if crontab -l 2>/dev/null | grep -q "aion.*backup.py"; then
    echo "Backup cron job already exists."
else
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
    echo "Backup cron job installed: daily at 1:00 AM"
    echo "  $CRON_CMD"
fi

echo ""
echo "Current crontab:"
crontab -l
```

## What NOT To Do

- Do NOT change any other part of debug.py (log_request, log_response, etc.)
- Do NOT modify the backup to touch any source files
- Do NOT change any other configuration

## Verification

### Log rotation:
1. Restart the server.
2. Send a message.
3. Check `data/logs/` — should have `debug.log` (current day).
4. The old `debug.log.1`, `debug.log.2` etc from the size-based rotation can be deleted manually.

### Backup:
1. Run `python backup.py`
2. Check `data/backups/` — should have a timestamped folder with archive.db, working.db, chromadb/, secrets.enc, .master_key.
3. Run `python backup.py` again — should create a second backup.

### Cron (optional, run when ready):
1. Review `setup_cron.sh` and adjust the PYTHON path if needed.
2. Run `bash setup_cron.sh`
3. Verify with `crontab -l`

## Done When

Logs rotate daily with 30 days retention. Backup script creates timestamped copies of all data. Cron job runs backup at 1:00 AM daily.
