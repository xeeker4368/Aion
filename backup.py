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
