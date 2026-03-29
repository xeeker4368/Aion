# CC Task 34 — Restore Database Backup

## Why

The database was wiped in Session 12 to disprove a theory about poisoned data. The theory was disproven — identity issues are not caused by bad data. The entity needs its accumulated memories back. The backup contains all experience from before the hermes3 switch.

## What to Do

### Step 1: Identify the backup to restore

```bash
ls -la /home/localadmin/aion/data/backups/ | head -20
```

We want the most recent backup from BEFORE the database wipe (before the hermes3 switch if possible). Look at the timestamps and pick the right one.

### Step 2: Stop the server

```bash
# Kill any running uvicorn/server process
pkill -f "uvicorn.*server" || true
```

### Step 3: Back up the CURRENT (empty) databases just in case

```bash
mkdir -p /home/localadmin/aion/data/backups/pre_restore_empty
cp /home/localadmin/aion/data/archive.db /home/localadmin/aion/data/backups/pre_restore_empty/ 2>/dev/null || true
cp /home/localadmin/aion/data/working.db /home/localadmin/aion/data/backups/pre_restore_empty/ 2>/dev/null || true
```

### Step 4: Restore from backup

Replace `BACKUP_DIR` with the actual backup directory name from Step 1:

```bash
BACKUP_DIR="/home/localadmin/aion/data/backups/REPLACE_WITH_CORRECT_BACKUP"

# Restore SQLite databases
cp "$BACKUP_DIR/archive.db" /home/localadmin/aion/data/archive.db
cp "$BACKUP_DIR/working.db" /home/localadmin/aion/data/working.db

# Restore ChromaDB
rm -rf /home/localadmin/aion/data/chromadb
cp -r "$BACKUP_DIR/chromadb" /home/localadmin/aion/data/chromadb
```

### Step 5: Verify

```bash
cd /home/localadmin/aion
source aion/bin/activate

python3 -c "
import sqlite3
conn = sqlite3.connect('data/archive.db')
count = conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
print(f'Archive messages: {count}')
conn.close()

conn = sqlite3.connect('data/working.db')
count = conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
convs = conn.execute('SELECT COUNT(*) FROM conversations').fetchone()[0]
print(f'Working messages: {count}')
print(f'Conversations: {convs}')
conn.close()

import chromadb
client = chromadb.PersistentClient(path='data/chromadb')
collection = client.get_or_create_collection('aion_memory')
print(f'ChromaDB chunks: {collection.count()}')
"
```

**Expected:** Non-zero counts for all three stores.

## What NOT to Do

- Do not restore the secrets.enc or .master_key from the backup — current vault keys should stay
- Do not start the server yet — Task 35 changes the model first
- Do not delete any backup directories

## Verification

All three stores show data. Archive messages > 0. ChromaDB chunks > 0.
