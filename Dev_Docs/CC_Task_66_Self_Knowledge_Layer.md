# CC Task 66 — Self-Knowledge Layer (Pattern Recognition)

Read this spec. Make exactly these changes. Nothing else.

## Overview

A new overnight step that reads all observer characterizations and journal entries, compares them against its own previous output, and produces an updated self-knowledge narrative. This narrative is loaded into the entity's context window so it knows what it has learned about itself over time.

This is NOT personality (how the entity communicates). This is self-knowledge — recurring patterns, corrections, growth, blind spots, trajectory.

Four files changed, one new file created.

---

## Change 1: db.py — Add self_knowledge table and functions

### 1a. Add table creation

Inside `init_databases()`, after the `overnight_runs` table creation (after line 121), add:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS self_knowledge (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                observation_count INTEGER,
                journal_count INTEGER,
                created_at TEXT NOT NULL
            )
        """)
```

### 1b. Add three functions

Add these after the `get_latest_overnight_run()` function (after line 493):

```python
def save_self_knowledge(content: str, observation_count: int,
                        journal_count: int) -> dict:
    """Save an updated self-knowledge narrative."""
    sk_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT INTO self_knowledge "
            "(id, content, observation_count, journal_count, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sk_id, content, observation_count, journal_count, now),
        )

    return {
        "id": sk_id,
        "content": content,
        "observation_count": observation_count,
        "journal_count": journal_count,
        "created_at": now,
    }


def get_latest_self_knowledge() -> dict | None:
    """Get the most recent self-knowledge narrative."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT * FROM self_knowledge ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_self_knowledge_history(limit: int = 10) -> list[dict]:
    """Get recent self-knowledge narratives, newest first."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM self_knowledge ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
```

---

## Change 2: Create pattern_recognition.py

Create this as a new file in the project root:

```python
"""
Aion Pattern Recognition (Self-Knowledge Layer)

Reads all observer characterizations and journal entries, compares
against its own previous output, and produces an updated self-knowledge
narrative. This gives the entity awareness of its own patterns,
corrections, growth, and blind spots.

The narrative is loaded into the context window — the entity reads it
as self-knowledge, not as a directive. What it does with the information
is up to it.

Uses qwen3:14b — same model as the observer and consolidation.
Runs as an overnight step after the observer.
"""

import logging
from datetime import datetime, timezone

import ollama

import db
import memory
from config import OLLAMA_HOST, CONSOLIDATION_MODEL

logger = logging.getLogger("aion.pattern_recognition")

PATTERN_MODEL = CONSOLIDATION_MODEL
PATTERN_CTX = 16384

PATTERN_PROMPT = """You are reviewing the behavioral history of an AI entity. Your job is to produce an updated self-knowledge narrative — a concise summary of what this entity has learned about itself through accumulated experience.

This is not a personality description. This is self-awareness: what patterns keep recurring, what mistakes keep happening, what's improving, what blind spots remain, and how the entity has changed over time.

Be specific. Reference actual events from the observations and journals. Avoid generic statements like "demonstrates a collaborative approach" — point to what actually happened.

Track change over time. If something improved or got worse compared to the previous narrative, say so. If a correction from an earlier conversation was repeated later, note that the lesson didn't stick. If a struggle from earlier conversations is no longer appearing, note that as growth.

Keep the narrative under 300 words. It will be loaded into the entity's context window, so every word must earn its place.
"""


def run_pattern_recognition() -> dict | None:
    """
    Run the self-knowledge synthesis.

    Reads all observations and journals, compares against the previous
    narrative, and produces an updated self-knowledge narrative.

    Returns the result dict if successful, None if not enough data.
    """
    # Gather observations
    observations = db.get_all_observations()
    if not observations:
        logger.info("No observations yet. Skipping pattern recognition.")
        return None

    # Gather journal entries from ChromaDB
    journals = _get_all_journals()

    # Get previous narrative (orient step)
    previous = db.get_latest_self_knowledge()

    logger.info(
        "Pattern recognition: %d observations, %d journal chunks, previous narrative: %s",
        len(observations), len(journals), "yes" if previous else "no",
    )

    # Build the prompt
    prompt_parts = [PATTERN_PROMPT]

    # Previous narrative (orient)
    if previous:
        prompt_parts.append(
            f"\nHere is the self-knowledge narrative from the last update "
            f"({previous['created_at'][:10]}, based on {previous['observation_count']} "
            f"observations and {previous['journal_count']} journals):\n\n"
            f"{previous['content']}"
        )
    else:
        prompt_parts.append(
            "\nThis is the first self-knowledge narrative. There is no previous version."
        )

    # Observations (chronological)
    prompt_parts.append("\n\nBEHAVIORAL OBSERVATIONS (chronological):\n")
    for obs in observations:
        date = obs.get("started_at", obs.get("created_at", "unknown"))[:10]
        msg_count = obs.get("message_count", 0)
        prompt_parts.append(f"\n[{date}, {msg_count} messages]\n{obs['content']}")

    # Journals (chronological)
    if journals:
        prompt_parts.append("\n\nJOURNAL ENTRIES (chronological):\n")
        for j in journals:
            prompt_parts.append(f"\n{j}")

    full_prompt = "\n".join(prompt_parts)

    # Check if it fits in context
    estimated_tokens = len(full_prompt) // 4
    if estimated_tokens > PATTERN_CTX - 2000:
        logger.warning(
            "Pattern recognition prompt too large (~%d tokens). "
            "Truncating journals.", estimated_tokens,
        )
        # Truncate journals first — observations are more valuable
        max_chars = (PATTERN_CTX - 2000) * 4
        full_prompt = full_prompt[:max_chars]

    logger.info("Sending to %s (~%d tokens)", PATTERN_MODEL, len(full_prompt) // 4)

    # Call the model
    try:
        client = ollama.Client(host=OLLAMA_HOST)
        response = client.chat(
            model=PATTERN_MODEL,
            messages=[{"role": "user", "content": full_prompt}],
            options={"num_ctx": PATTERN_CTX},
        )
        narrative = response["message"]["content"].strip()
    except Exception as e:
        logger.error("Pattern recognition model call failed: %s", e)
        return None

    if not narrative:
        logger.error("Pattern recognition returned empty narrative.")
        return None

    logger.info("Self-knowledge narrative: %s", narrative[:200])

    # Store in working.db
    result = db.save_self_knowledge(
        content=narrative,
        observation_count=len(observations),
        journal_count=len(journals),
    )

    logger.info("Self-knowledge narrative stored in DB2.")
    return result


def _get_all_journals() -> list[str]:
    """
    Retrieve all journal entry texts from ChromaDB, in chronological order.

    Journals are stored as documents with source_type='journal'.
    Each journal may have multiple chunks — we concatenate per document.
    """
    collection = memory._get_collection()

    try:
        results = collection.get(
            where={"source_type": "journal"},
            include=["documents", "metadatas"],
        )
    except Exception as e:
        logger.error("Failed to retrieve journals from ChromaDB: %s", e)
        return []

    if not results or not results["documents"]:
        return []

    # Group chunks by conversation_id (doc_id), sort by chunk_index
    docs = {}
    for i, doc_text in enumerate(results["documents"]):
        meta = results["metadatas"][i] if results["metadatas"] else {}
        doc_id = meta.get("conversation_id", "unknown")
        chunk_index = meta.get("chunk_index", 0)
        created_at = meta.get("created_at", "")

        if doc_id not in docs:
            docs[doc_id] = {"chunks": [], "created_at": created_at}
        docs[doc_id]["chunks"].append((chunk_index, doc_text))

    # Sort documents by created_at, concatenate chunks within each
    sorted_docs = sorted(docs.items(), key=lambda x: x[1]["created_at"])

    journal_texts = []
    for doc_id, data in sorted_docs:
        chunks = sorted(data["chunks"], key=lambda x: x[0])
        full_text = "\n".join(text for _, text in chunks)
        date = data["created_at"][:10] if data["created_at"] else "unknown"
        journal_texts.append(f"[Journal — {date}]\n{full_text}")

    return journal_texts
```

---

## Change 3: overnight.py — Add pattern recognition step

### 3a. Add import

After line 26 (`from observer import run_observer`), add:

```python
from pattern_recognition import run_pattern_recognition
```

### 3b. Add Step 3.5

After the observer step (after line 149, after `run_data["observer_summary"] = ...`), add:

```python
    # Step 3.5: Self-Knowledge (Pattern Recognition)
    logger.info("--- Step 3.5: Self-Knowledge ---")
    try:
        result = run_pattern_recognition()
        if result:
            run_data["self_knowledge_status"] = "success"
            run_data["self_knowledge_summary"] = (
                f"Narrative updated ({result['observation_count']} observations, "
                f"{result['journal_count']} journals)"
            )
            logger.info(f"Self-knowledge: {run_data['self_knowledge_summary']}")
        else:
            run_data["self_knowledge_status"] = "skipped"
            run_data["self_knowledge_summary"] = "Not enough data"
            logger.info("Self-knowledge: not enough data yet.")
    except Exception as e:
        logger.error(f"Self-knowledge failed: {e}")
        run_data["self_knowledge_status"] = "failed"
        run_data["self_knowledge_summary"] = str(e)[:200]
```

### 3c. Update overnight_runs table

The overnight_runs table in db.py needs two new columns. Add to the `init_databases()` function, inside the `_migrate_working_db` function (or after the overnight_runs CREATE TABLE):

Actually — the simplest approach: add a migration. In `_migrate_working_db()`, after the existing migrations (after line 138), add:

```python
    # Add self-knowledge columns to overnight_runs if upgrading
    on_columns = {row[1] for row in conn.execute("PRAGMA table_info(overnight_runs)")}
    if "self_knowledge_status" not in on_columns:
        conn.execute(
            "ALTER TABLE overnight_runs ADD COLUMN self_knowledge_status TEXT DEFAULT 'skipped'"
        )
        conn.execute(
            "ALTER TABLE overnight_runs ADD COLUMN self_knowledge_summary TEXT"
        )
```

### 3d. Update save_overnight_run in db.py

Replace the `save_overnight_run` function (lines 457-474) with:

```python
def save_overnight_run(run_data: dict):
    """Save an overnight run record."""
    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT INTO overnight_runs "
            "(id, started_at, ended_at, duration_seconds, conversations_closed, "
            "research_status, research_summary, journal_status, journal_summary, "
            "observer_status, observer_summary, "
            "self_knowledge_status, self_knowledge_summary, "
            "consolidation_status, consolidation_summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_data["id"], run_data["started_at"], run_data.get("ended_at"),
                run_data.get("duration_seconds"), run_data.get("conversations_closed", 0),
                run_data.get("research_status", "skipped"), run_data.get("research_summary"),
                run_data.get("journal_status", "skipped"), run_data.get("journal_summary"),
                run_data.get("observer_status", "skipped"), run_data.get("observer_summary"),
                run_data.get("self_knowledge_status", "skipped"), run_data.get("self_knowledge_summary"),
                run_data.get("consolidation_status", "skipped"), run_data.get("consolidation_summary"),
            ),
        )
```

---

## Change 4: chat.py — Load self-knowledge into context window

### 4a. Add import

After line 7 (`import ollama`), add:

```python
import db
```

### 4b. Add self-knowledge to build_system_prompt

Replace the section from the behavioral guidance comment through SOUL.md (lines 98-106) with:

```python
    # --- Single-user context (factual) ---
    parts.append("""

You are a single-user system. The person you are talking to right now is the same person from all of your memories. What you remember about them is what you know about them — use it naturally, the way you would remember a friend.""")

    # --- Self-knowledge (what you've learned about yourself) ---
    self_knowledge = db.get_latest_self_knowledge()
    if self_knowledge:
        parts.append(
            f"\n\nWhat you have learned about yourself through experience:\n\n"
            f"{self_knowledge['content']}"
        )

    # --- Identity (soul.md) — last, closest to generation ---
    soul = load_soul()
    parts.append(f"\n\n{soul}")
```

### 4c. Update the docstring

Replace the docstring (lines 57-65) with:

```python
    """
    Assemble the system prompt from identity and memory.

    The model sees (in order):
    1. Remembered experiences (conversation chunks from ChromaDB)
    2. Available skills
    3. Single-user context (factual)
    4. Self-knowledge (what the entity has learned about itself)
    5. Identity (soul.md) — LAST, closest to generation
    """
```

---

## What NOT to Do

- Do NOT store the self-knowledge narrative in ChromaDB. It is loaded into the context window directly — not searched. Old narratives contradict current ones and would confuse retrieval.
- Do NOT add any behavioral directives. The self-knowledge narrative is factual information, not instructions.
- Do NOT change the observer, journal, research, or consolidation logic.
- Do NOT change SOUL.md.
- Do NOT change the token budgets — the narrative fits within the existing SOUL budget.

## Verification

1. **Database**: Start the server. Check that `self_knowledge` table exists: `sqlite3 data/working.db ".tables"` should list it.
2. **First run**: Run `python overnight.py --dev`. Check logs for "Step 3.5: Self-Knowledge" with a narrative produced. Check `data/dev/working.db`: `SELECT content FROM self_knowledge ORDER BY created_at DESC LIMIT 1;`
3. **Context injection**: Start the server in dev mode. Send a message. Check the debug log — the full system prompt should contain "What you have learned about yourself through experience:" followed by the narrative, positioned between the single-user context and SOUL.md.
4. **Second run**: Run overnight again. The new narrative should reference the previous one ("the previous narrative noted..."). Check that `self_knowledge` table now has 2 rows.
5. **No data case**: On a fresh database with no observations, the pattern recognizer should skip gracefully: "No observations yet. Skipping pattern recognition."
