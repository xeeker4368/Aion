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


def _backup_sqlite(source: Path, dest: Path):
    """
    Safe SQLite backup using the backup API.
    Handles WAL mode and concurrent writes correctly.
    """
    import sqlite3

    source_conn = sqlite3.connect(str(source))
    dest_conn = sqlite3.connect(str(dest))
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()


def run_backup(backup_root: Path = None):
    if backup_root is None:
        backup_root = DEFAULT_BACKUP_DIR

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    backup_dir = backup_root / f"aion_backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    print(f"Backing up to {backup_dir}...")

    # Archive DB — use SQLite backup API for safe copy
    if ARCHIVE_DB.exists():
        _backup_sqlite(ARCHIVE_DB, backup_dir / "archive.db")
        size = (backup_dir / "archive.db").stat().st_size / 1024
        print(f"  archive.db: {size:.1f} KB")

    # Working DB — use SQLite backup API for safe copy
    if WORKING_DB.exists():
        _backup_sqlite(WORKING_DB, backup_dir / "working.db")
        size = (backup_dir / "working.db").stat().st_size / 1024
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
