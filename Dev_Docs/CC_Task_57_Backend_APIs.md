# CC Task 57: Backend APIs for New UI

**Priority:** Required for UI — nothing else until this is done
**Risk:** Medium — adds new tables, endpoints, and config system
**Files to modify:** db.py, server.py, overnight.py, config.py
**New files:** data/config.json (created at runtime)

---

## Overview

The new UI needs backend APIs for: system health, memory stats, document content, observations, overnight run history, and editable configuration. This task adds all of them.

---

## Part 1: Overnight Run History

### Add table to db.py

Add this table creation in `init_databases()` inside the working DB section, after the observations table:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS overnight_runs (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_seconds REAL,
                conversations_closed INTEGER DEFAULT 0,
                research_status TEXT DEFAULT 'skipped',
                research_summary TEXT,
                journal_status TEXT DEFAULT 'skipped',
                journal_summary TEXT,
                observer_status TEXT DEFAULT 'skipped',
                observer_summary TEXT,
                consolidation_status TEXT DEFAULT 'skipped',
                consolidation_summary TEXT
            )
        """)
```

### Add db functions

Add these functions to db.py:

```python
def save_overnight_run(run_data: dict):
    """Save an overnight run record."""
    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT INTO overnight_runs "
            "(id, started_at, ended_at, duration_seconds, conversations_closed, "
            "research_status, research_summary, journal_status, journal_summary, "
            "observer_status, observer_summary, consolidation_status, consolidation_summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_data["id"], run_data["started_at"], run_data.get("ended_at"),
                run_data.get("duration_seconds"), run_data.get("conversations_closed", 0),
                run_data.get("research_status", "skipped"), run_data.get("research_summary"),
                run_data.get("journal_status", "skipped"), run_data.get("journal_summary"),
                run_data.get("observer_status", "skipped"), run_data.get("observer_summary"),
                run_data.get("consolidation_status", "skipped"), run_data.get("consolidation_summary"),
            ),
        )


def get_overnight_runs(limit: int = 10) -> list[dict]:
    """Get recent overnight runs, newest first."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM overnight_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_overnight_run() -> dict | None:
    """Get the most recent overnight run."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT * FROM overnight_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
```

### Update overnight.py to save run results

Replace the `run_overnight()` function with this version that captures results:

```python
def run_overnight():
    """Run all overnight processes in order."""
    start = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    logger.info("=" * 60)
    logger.info("OVERNIGHT CYCLE STARTING")
    logger.info("=" * 60)

    # Init systems
    db.init_databases()
    memory.init_memory()
    vault.init_secrets()
    executors.init_executors()
    skills.init_skills()

    run_data = {
        "id": run_id,
        "started_at": start.isoformat(),
    }

    # Step 0: Close all active conversations
    logger.info('--- Step 0: Close Active Conversations ---')
    try:
        closed = _end_active_conversations()
        run_data["conversations_closed"] = closed
        logger.info(f'Closed {closed} active conversation(s).' if closed else 'No active conversations.')
    except Exception as e:
        logger.error(f'Failed to close conversations: {e}')
        run_data["conversations_closed"] = 0

    # Step 1: Autonomous Research
    logger.info("--- Step 1: Research ---")
    try:
        result = run_research()
        if result:
            run_data["research_status"] = "skipped" if result.get("skipped") else "success"
            run_data["research_summary"] = (
                f"{result['tool_calls']} tool calls, {result['stored_chars']} chars stored"
            )
            logger.info(f"Research complete: {run_data['research_summary']}")
        else:
            run_data["research_status"] = "skipped"
            run_data["research_summary"] = "Nothing to explore"
            logger.info("Research: nothing to explore.")
    except Exception as e:
        logger.error(f"Research failed: {e}")
        run_data["research_status"] = "failed"
        run_data["research_summary"] = str(e)[:200]

    # Step 2: Journal
    logger.info("--- Step 2: Journal ---")
    try:
        result = run_journal()
        if result:
            run_data["journal_status"] = "success"
            run_data["journal_summary"] = (
                f"Reflected on {result['experience_chars']} chars of experiences"
            )
            logger.info(f"Journal entry written: {run_data['journal_summary']}")
        else:
            run_data["journal_status"] = "skipped"
            run_data["journal_summary"] = "Nothing to reflect on"
            logger.info("Journal: nothing to reflect on.")
    except Exception as e:
        logger.error(f"Journal failed: {e}")
        run_data["journal_status"] = "failed"
        run_data["journal_summary"] = str(e)[:200]

    # Step 3: Personality observer
    logger.info("--- Step 3: Personality Observer ---")
    try:
        results = run_observer()
        if results:
            run_data["observer_status"] = "success"
            run_data["observer_summary"] = f"{len(results)} conversations characterized"
            for obs in results:
                logger.info(f"  Observed conversation {obs['conversation_id']}: {obs['message_count']} messages")
            logger.info(f"Observer: {run_data['observer_summary']}")
        else:
            run_data["observer_status"] = "skipped"
            run_data["observer_summary"] = "Nothing to observe"
            logger.info("Observer: nothing to observe.")
    except Exception as e:
        logger.error(f"Observer failed: {e}")
        run_data["observer_status"] = "failed"
        run_data["observer_summary"] = str(e)[:200]

    # Step 4: Consolidation
    logger.info("--- Step 4: Consolidation ---")
    try:
        pending = db.get_unconsolidated_conversations()
        consolidate_pending()
        count = len(pending) if pending else 0
        run_data["consolidation_status"] = "success" if count > 0 else "skipped"
        run_data["consolidation_summary"] = (
            f"{count} conversations summarized" if count > 0 else "Nothing pending"
        )
    except Exception as e:
        logger.error(f"Consolidation failed: {e}")
        run_data["consolidation_status"] = "failed"
        run_data["consolidation_summary"] = str(e)[:200]

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    run_data["ended_at"] = datetime.now(timezone.utc).isoformat()
    run_data["duration_seconds"] = round(elapsed, 1)

    # Save run record
    try:
        db.save_overnight_run(run_data)
        logger.info("Overnight run record saved.")
    except Exception as e:
        logger.error(f"Failed to save run record: {e}")

    logger.info("=" * 60)
    logger.info(f"OVERNIGHT CYCLE COMPLETE ({elapsed:.1f}s)")
    logger.info("=" * 60)
```

Add `import uuid` to overnight.py imports if not already present.

---

## Part 2: Config System

### Create a get/set config mechanism

Add a new file `config_manager.py`:

```python
"""
Aion Config Manager

Reads and writes editable configuration to data/config.json.
Values in config.json override defaults from config.py.
The UI reads and writes through this module.
"""

import json
import logging
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger("aion.config_manager")

CONFIG_FILE = DATA_DIR / "config.json"

# Editable settings with their defaults and types
EDITABLE_SETTINGS = {
    "OLLAMA_HOST": {"default": "http://localhost:11434", "type": "string"},
    "CHAT_MODEL": {"default": "llama3.1:8b-aion", "type": "string"},
    "CONSOLIDATION_MODEL": {"default": "qwen3:14b", "type": "string"},
    "EMBED_MODEL": {"default": "nomic-embed-text", "type": "string"},
    "CONTEXT_WINDOW": {"default": 10240, "type": "integer"},
    "LIVE_CHUNK_INTERVAL": {"default": 10, "type": "integer"},
    "RETRIEVAL_RESULTS": {"default": 5, "type": "integer"},
    "SEARCH_MONTHLY_LIMIT": {"default": 1000, "type": "integer"},
}


def _load() -> dict:
    """Load config.json, return empty dict if missing."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save(data: dict):
    """Save config to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def get_all() -> dict:
    """Get all editable settings with current values."""
    overrides = _load()
    result = {}
    for key, meta in EDITABLE_SETTINGS.items():
        result[key] = {
            "value": overrides.get(key, meta["default"]),
            "default": meta["default"],
            "type": meta["type"],
            "modified": key in overrides,
        }
    return result


def get(key: str):
    """Get a single config value (override or default)."""
    overrides = _load()
    meta = EDITABLE_SETTINGS.get(key)
    if meta is None:
        return None
    return overrides.get(key, meta["default"])


def update(key: str, value) -> bool:
    """Update a single config value. Returns True if valid."""
    meta = EDITABLE_SETTINGS.get(key)
    if meta is None:
        return False

    # Type coercion
    if meta["type"] == "integer":
        try:
            value = int(value)
        except (ValueError, TypeError):
            return False
    elif meta["type"] == "string":
        value = str(value)

    overrides = _load()
    overrides[key] = value
    _save(overrides)
    logger.info(f"Config updated: {key} = {value}")
    return True


def reset(key: str) -> bool:
    """Reset a config value to its default."""
    overrides = _load()
    if key in overrides:
        del overrides[key]
        _save(overrides)
        logger.info(f"Config reset to default: {key}")
        return True
    return False
```

### Update config.py to read from config.json

Replace the Ollama and tuning sections of config.py (after the paths section) with:

```python
# --- Load overrides from config.json ---
import json as _json

_CONFIG_FILE = DATA_DIR / "config.json"
_overrides = {}
if _CONFIG_FILE.exists():
    try:
        _overrides = _json.loads(_CONFIG_FILE.read_text())
    except Exception:
        pass

# --- Ollama ---
OLLAMA_HOST = _overrides.get("OLLAMA_HOST", "http://localhost:11434")
CHAT_MODEL = _overrides.get("CHAT_MODEL", "llama3.1:8b-aion")
CONSOLIDATION_MODEL = _overrides.get("CONSOLIDATION_MODEL", "qwen3:14b")
EMBED_MODEL = _overrides.get("EMBED_MODEL", "nomic-embed-text")

# --- Context Window Budget ---
CONTEXT_WINDOW = _overrides.get("CONTEXT_WINDOW", 10240)
SOUL_TOKEN_BUDGET = 663
RETRIEVAL_TOKEN_BUDGET = 1500
RESPONSE_TOKEN_BUDGET = 1000
CONVERSATION_TOKEN_BUDGET = (
    CONTEXT_WINDOW
    - SOUL_TOKEN_BUDGET
    - RETRIEVAL_TOKEN_BUDGET
    - RESPONSE_TOKEN_BUDGET
)

# --- Chunking ---
LIVE_CHUNK_INTERVAL = _overrides.get("LIVE_CHUNK_INTERVAL", 10)

# --- Retrieval ---
RETRIEVAL_RESULTS = _overrides.get("RETRIEVAL_RESULTS", 5)

# --- Search Rate Limiting ---
SEARCH_MONTHLY_LIMIT = _overrides.get("SEARCH_MONTHLY_LIMIT", 1000)

# --- Document Ingestion ---
INGEST_CHUNK_SIZE = 1500
INGEST_CHUNK_OVERLAP = 200
```

**Important:** Config changes require a server restart to take effect. The UI should tell the user this.

---

## Part 3: New API Endpoints

Add these endpoints to server.py. Add `import config_manager` at the top with the other imports.

### Health check

```python
@app.get("/api/health")
async def health_check():
    """System health check — Ollama, ChromaDB, overnight status, dev mode."""
    health = {"dev_mode": DEV_MODE}

    # Ollama
    try:
        import requests
        resp = requests.get(f"{config.OLLAMA_HOST}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        health["ollama"] = {"status": "ok", "models": models}
    except Exception as e:
        health["ollama"] = {"status": "down", "error": str(e)[:100]}

    # ChromaDB
    try:
        count = memory._get_collection().count()
        health["chromadb"] = {"status": "ok", "chunks": count}
    except Exception as e:
        health["chromadb"] = {"status": "down", "error": str(e)[:100]}

    # Overnight
    latest_run = db.get_latest_overnight_run()
    if latest_run:
        health["overnight"] = {
            "last_run": latest_run["started_at"],
            "duration": latest_run.get("duration_seconds"),
            "status": "ok",
        }
        # Flag if last run was more than 26 hours ago
        from datetime import datetime, timezone, timedelta
        try:
            last = datetime.fromisoformat(latest_run["started_at"])
            if datetime.now(timezone.utc) - last > timedelta(hours=26):
                health["overnight"]["status"] = "stale"
        except Exception:
            pass
    else:
        health["overnight"] = {"status": "never_run"}

    return health
```

### Memory stats

```python
@app.get("/api/memory/stats")
async def memory_stats():
    """Memory system statistics."""
    collection = memory._get_collection()

    # Total chunks
    total = collection.count()

    # Chunks by type
    type_counts = {}
    for source_type in ["conversation", "research", "journal", "observation", "article"]:
        try:
            results = collection.get(where={"source_type": source_type}, include=[])
            type_counts[source_type] = len(results["ids"])
        except Exception:
            type_counts[source_type] = 0

    # Conversations and documents from DB2
    with db._connect(db.WORKING_DB) as conn:
        conv_count = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE ended_at IS NOT NULL"
        ).fetchone()[0]
        doc_count = conn.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0]
        obs_count = conn.execute(
            "SELECT COUNT(*) FROM observations"
        ).fetchone()[0]

    # Storage sizes
    import os
    storage = {}
    for name, path in [("archive_db", config.ARCHIVE_DB), ("working_db", config.WORKING_DB)]:
        try:
            storage[name] = os.path.getsize(str(path))
        except OSError:
            storage[name] = 0

    chroma_path = Path(config.CHROMA_DIR)
    if chroma_path.exists():
        storage["chromadb"] = sum(f.stat().st_size for f in chroma_path.rglob("*") if f.is_file())
    else:
        storage["chromadb"] = 0

    return {
        "total_chunks": total,
        "chunks_by_type": type_counts,
        "conversations": conv_count,
        "documents": doc_count,
        "observations": obs_count,
        "storage": storage,
    }
```

### Documents by type

```python
@app.get("/api/documents")
async def list_documents(source_type: str = None):
    """List documents, optionally filtered by type."""
    with db._connect(db.WORKING_DB) as conn:
        if source_type:
            rows = conn.execute(
                "SELECT * FROM documents WHERE source_type = ? ORDER BY created_at DESC",
                (source_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()
    return {"documents": [dict(row) for row in rows]}


@app.get("/api/documents/{doc_id}/content")
async def get_document_content(doc_id: str):
    """Get full document content from ChromaDB chunks."""
    collection = memory._get_collection()
    try:
        results = collection.get(
            where={"conversation_id": doc_id},
            include=["documents", "metadatas"],
        )
    except Exception:
        return {"content": "", "chunks": 0}

    if not results or not results["documents"]:
        return {"content": "", "chunks": 0}

    # Sort by chunk_index and concatenate
    chunks = list(zip(results["documents"], results["metadatas"]))
    chunks.sort(key=lambda x: x[1].get("chunk_index", 0))
    content = "\n\n".join(doc for doc, _ in chunks)
    return {"content": content, "chunks": len(chunks)}
```

### Observations

```python
@app.get("/api/observations")
async def list_observations():
    """List all personality observations."""
    observations = db.get_all_observations()
    return {"observations": observations}
```

### Overnight runs

```python
@app.get("/api/overnight/runs")
async def list_overnight_runs(limit: int = 10):
    """Get recent overnight run history."""
    runs = db.get_overnight_runs(limit)
    return {"runs": runs}
```

### Config

```python
@app.get("/api/config")
async def get_config():
    """Get all editable configuration."""
    import config_manager
    return {"config": config_manager.get_all()}


@app.put("/api/config/{key}")
async def update_config(key: str, request: dict):
    """Update a config value. Requires server restart to take effect."""
    import config_manager
    value = request.get("value")
    if value is None:
        return JSONResponse(status_code=400, content={"error": "Missing 'value'"})

    if config_manager.update(key, value):
        return {"status": "updated", "key": key, "value": value, "restart_required": True}
    return JSONResponse(status_code=400, content={"error": f"Invalid key or value: {key}"})


@app.delete("/api/config/{key}")
async def reset_config(key: str):
    """Reset a config value to default."""
    import config_manager
    if config_manager.reset(key):
        return {"status": "reset", "key": key, "restart_required": True}
    return JSONResponse(status_code=404, content={"error": f"Key not found or already default: {key}"})
```

### Conversation transcript

```python
@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get a conversation with its messages and summary."""
    messages = db.get_conversation_messages(conversation_id)
    summary = db.get_summary(conversation_id)

    return {
        "conversation_id": conversation_id,
        "messages": [dict(m) for m in messages],
        "summary": summary,
    }
```

---

## Part 4: Add PathLib import to server.py

Add `from pathlib import Path` to the server.py imports if not already present (needed for memory stats storage size calculation).

Also add `import config` to server.py imports if not already present (needed for health check and memory stats).

---

## What NOT to Do

- Do NOT change any existing endpoints.
- Do NOT change the chat flow, memory system, or personality system.
- Do NOT change SOUL.md or any skill files.
- Do NOT add authentication — this is a single-user local system.
- Do NOT make config changes take effect without a restart — that would require reimporting modules at runtime which is fragile.

---

## Verification

```bash
# 1. Start server and check health
curl http://localhost:8000/api/health | python -m json.tool

# 2. Check memory stats
curl http://localhost:8000/api/memory/stats | python -m json.tool

# 3. Check config
curl http://localhost:8000/api/config | python -m json.tool

# 4. Update a config value
curl -X PUT http://localhost:8000/api/config/RETRIEVAL_RESULTS \
  -H "Content-Type: application/json" \
  -d '{"value": 7}' | python -m json.tool

# 5. Check documents
curl "http://localhost:8000/api/documents?source_type=journal" | python -m json.tool

# 6. Check observations
curl http://localhost:8000/api/observations | python -m json.tool

# 7. Check overnight runs (will be empty until overnight runs once)
curl http://localhost:8000/api/overnight/runs | python -m json.tool

# 8. Run overnight manually to generate a run record
python overnight.py

# 9. Check overnight runs again
curl http://localhost:8000/api/overnight/runs | python -m json.tool
```
