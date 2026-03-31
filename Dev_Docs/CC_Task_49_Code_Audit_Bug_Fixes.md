# CC Task 49: Code Audit Bug Fixes

**Priority:** Pre-go-live — all of these must be fixed
**Risk:** Low-medium — each fix is isolated
**Files to modify:** server.py, backup.py, vault.py, research.py

---

## BUG-1: dist format crash on missing distance (server.py)

**The problem:** Line 424 uses `:.4f` format on a value that might be the string `"?"`, which raises `ValueError`.

**Current code (line 422-424):**
```python
        for i, chunk in enumerate(retrieved_chunks):
            dist = chunk.get("distance", "?")
            logger.info(f"  Chunk {i}: [{dist:.4f}] {preview}...")
```

**Replace with:**
```python
        for i, chunk in enumerate(retrieved_chunks):
            dist = chunk.get("distance")
            dist_str = f"{dist:.4f}" if isinstance(dist, (int, float)) else "?"
            preview = chunk.get("text", "")[:80].replace("\n", " ")
            logger.info(f"  Chunk {i}: [{dist_str}] {preview}...")
```

**Note:** The `preview` variable is already defined on the next line in the original code. Make sure the order stays: dist, dist_str, preview, then the log line.

---

## BUG-2: Corrupt backups from shutil.copy2 on live SQLite (backup.py)

**The problem:** `shutil.copy2` copies files byte-by-byte. If SQLite is mid-write (WAL mode, journal), the copy can be corrupt. The backup runs at 5:30am, overnight runs at 5:00am. If consolidation (qwen3:14b, slow) is still running, the databases are being written to.

**Replace the entire `run_backup` function with:**

```python
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
```

**Add this new function above `run_backup`:**

```python
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
```

**Note:** `shutil.copy2` is still fine for secrets.enc, .master_key, and the ChromaDB directory — those aren't SQLite databases. Only the two `.db` files need the backup API.

---

## BUG-3: Repeated init on decryption failure (vault.py)

**The problem:** If `init_secrets()` fails to decrypt, `_secrets` stays `{}`. Then `get()`, `list_keys()`, and `has()` all check `if not _secrets and SECRETS_FILE.exists()` and re-trigger `init_secrets()` on every call, logging the error each time.

**Fix:** Add a flag to track whether init has been attempted, regardless of whether it succeeded.

**Add a module-level variable after line 34:**
```python
_initialized: bool = False
```

**Replace `init_secrets()` (lines 64-81) with:**
```python
def init_secrets():
    """Initialize the secrets manager. Load and decrypt existing secrets."""
    global _fernet, _secrets, _initialized

    _initialized = True
    _fernet = Fernet(_get_master_key())

    if SECRETS_FILE.exists():
        try:
            encrypted = SECRETS_FILE.read_bytes()
            decrypted = _fernet.decrypt(encrypted)
            _secrets = json.loads(decrypted.decode())
            logger.info(f"Loaded {len(_secrets)} secrets")
        except Exception as e:
            logger.error(f"Failed to decrypt secrets: {e}")
            _secrets = {}
    else:
        _secrets = {}
        logger.info("No secrets file found, starting fresh")
```

**Replace the guard in `get()` (lines 96-100) with:**
```python
def get(key: str) -> str | None:
    """Get a secret by key name. Returns None if not found."""
    if not _initialized:
        init_secrets()
    return _secrets.get(key)
```

**Replace the guard in `list_keys()` (lines 120-124) with:**
```python
def list_keys() -> list[str]:
    """List all secret key names (not values)."""
    if not _initialized:
        init_secrets()
    return list(_secrets.keys())
```

**Replace the guard in `has()` (lines 127-131) with:**
```python
def has(key: str) -> bool:
    """Check if a secret exists."""
    if not _initialized:
        init_secrets()
    return key in _secrets
```

**Also remove the unused import on line 21:**
```python
# DELETE this line:
from base64 import urlsafe_b64encode
```

---

## BUG-4: Over-broad error detection in research (research.py)

**The problem:** The error guard matches the substring `'error'` anywhere in tool results. A search result containing "common errors in Python" or "no errors found" triggers the guard and discards valid research.

The fix: check for the specific error prefixes that executors.py actually produces, not grep the content.

**Current code (lines 103-108):**
```python
    if tool_calls_made:
        failed_tools = [
            tc for tc in tool_calls_made
            if any(err in tc.get('result', '').lower()
                   for err in ['error', 'failed', 'http 500', 'http 4', 'http 5'])
        ]
```

**Replace with:**
```python
    if tool_calls_made:
        # Check for executor error patterns — these are the exact patterns
        # that executors.py produces when something fails. Do NOT match
        # on content words like 'error' that could appear in valid results.
        error_prefixes = [
            'error:', 'error executing',
            'failed to fetch', 'http request failed',
            'search failed', 'search limit reached',
        ]
        # Also catch HTTP 4xx/5xx from http_request executor
        # (returns "HTTP 500\n..." or "HTTP 404\n..." etc.)
        http_error_prefixes = ['http 4', 'http 5']

        def _is_tool_error(result: str) -> bool:
            r = result.lower()
            if any(r.startswith(p) for p in error_prefixes):
                return True
            if any(r.startswith(p) for p in http_error_prefixes):
                return True
            return False

        failed_tools = [
            tc for tc in tool_calls_made
            if _is_tool_error(tc.get('result', ''))
        ]
```

---

## What NOT to Do

- Do NOT change any logic beyond what's described above.
- Do NOT modify the ChromaDB backup method — `shutil.copytree` is fine for a directory.
- Do NOT add file locking to search_limiter.py — unnecessary for single-user.
- Do NOT change async/sync patterns in server.py — works fine for single-user.
- Do NOT touch SOUL.md, chat.py, memory.py, db.py, or any overnight modules.

---

## Verification

### BUG-1:
```python
python -c "
dist = None
dist_str = f'{dist:.4f}' if isinstance(dist, (int, float)) else '?'
print(f'None -> {dist_str}')
dist = 0.3456
dist_str = f'{dist:.4f}' if isinstance(dist, (int, float)) else '?'
print(f'0.3456 -> {dist_str}')
"
# Should print: None -> ?  and  0.3456 -> 0.3456
```

### BUG-2:
```bash
# Create a test database and verify backup works
python -c "
import sqlite3, tempfile, os
from pathlib import Path
# Create source
src = Path(tempfile.mktemp(suffix='.db'))
conn = sqlite3.connect(str(src))
conn.execute('CREATE TABLE test (x TEXT)')
conn.execute('INSERT INTO test VALUES (\"hello\")')
conn.commit()
conn.close()
# Backup using the new function
from backup import _backup_sqlite
dst = Path(tempfile.mktemp(suffix='.db'))
_backup_sqlite(src, dst)
# Verify
conn = sqlite3.connect(str(dst))
row = conn.execute('SELECT * FROM test').fetchone()
print(f'Backup content: {row}')
conn.close()
os.unlink(str(src))
os.unlink(str(dst))
"
# Should print: Backup content: ('hello',)
```

### BUG-3:
```python
python -c "
import vault
vault.init_secrets()
# Call get multiple times — should NOT log repeated errors
print(vault.get('nonexistent'))
print(vault.get('nonexistent'))
print(vault.list_keys())
"
```

### BUG-4:
```python
python -c "
error_prefixes = [
    'error:', 'error executing',
    'failed to fetch', 'http request failed',
    'search failed', 'search limit reached',
]
http_error_prefixes = ['http 4', 'http 5']

def _is_tool_error(result):
    r = result.lower()
    if any(r.startswith(p) for p in error_prefixes):
        return True
    if any(r.startswith(p) for p in http_error_prefixes):
        return True
    return False

# Should match (real errors)
for test in ['Error: executor not found', 'Failed to fetch URL: timeout', 'Search failed: connection', 'HTTP 500\nInternal Server Error', 'HTTP 404\nNot Found']:
    print(f'  MATCH {_is_tool_error(test):5}  {test[:50]}')

# Should NOT match (valid content)
for test in ['No errors found in the code', 'The error rate decreased by 50%', 'HTTP 200\nOK results here']:
    print(f'  MATCH {_is_tool_error(test):5}  {test[:50]}')
"
```
