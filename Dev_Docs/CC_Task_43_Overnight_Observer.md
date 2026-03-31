# CC Task 43 — Overnight Orchestrator + Personality Observer

## What This Is

Build `overnight.py` — the batch script that runs during Lyle's sleep. For now it does two things: consolidation (already exists) and the personality observer (new). The observer reads completed conversations as a neutral third party and produces a free-form narrative characterization of the entity's behavior.

This is called by cron at 5am daily (cron is already set up for backup — overnight.py will be added as a separate cron entry).

## Files to Create

### `/home/localadmin/aion/overnight.py`

```python
"""
Aion Overnight Cycle

Runs batch processes while Lyle sleeps:
1. Consolidation — summaries for any pending conversations (UI only)
2. Personality observer — reads today's transcripts, characterizes the entity's behavior

Called by cron at 5am daily. Can also be run manually:
    python overnight.py
"""

import logging
from datetime import datetime, timezone

import db
import memory
from consolidation import consolidate_pending
from observer import run_observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
logger = logging.getLogger("aion.overnight")


def run_overnight():
    """Run all overnight processes in order."""
    start = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("OVERNIGHT CYCLE STARTING")
    logger.info("=" * 60)

    # Init systems
    db.init_databases()
    memory.init_memory()

    # Step 1: Consolidation (summaries for UI)
    logger.info("--- Step 1: Consolidation ---")
    try:
        consolidate_pending()
    except Exception as e:
        logger.error(f"Consolidation failed: {e}")

    # Step 2: Personality observer
    logger.info("--- Step 2: Personality Observer ---")
    try:
        result = run_observer()
        if result:
            logger.info(f"Observer produced characterization: {len(result['content'])} chars")
            logger.info(f"Based on {result['conversation_count']} conversations, {result['message_count']} messages")
        else:
            logger.info("Observer: nothing to observe (no conversations in last 24 hours)")
    except Exception as e:
        logger.error(f"Observer failed: {e}")

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info("=" * 60)
    logger.info(f"OVERNIGHT CYCLE COMPLETE ({elapsed:.1f}s)")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_overnight()
```

### `/home/localadmin/aion/observer.py`

```python
"""
Aion Personality Observer

A neutral third party reads the day's conversation transcripts and
characterizes the entity's behavior in free-form narrative. The observer
does not know it is evaluating itself. It does not use predefined
dimensions, scoring systems, or structured categories.

The characterization is stored in DB2 (for the profile generator to
read later) and chunked into ChromaDB (so the entity can eventually
search its own behavioral history).

Uses qwen3:14b — a different model from the chat model, ensuring
the observer is genuinely external.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

import ollama

import db
import memory
from config import OLLAMA_HOST, CONSOLIDATION_MODEL

logger = logging.getLogger("aion.observer")

# Use the same model and context window as consolidation
OBSERVER_MODEL = CONSOLIDATION_MODEL
OBSERVER_CTX = 16384

OBSERVER_PROMPT = """Read the following conversation transcript between a human and an AI. Based on what you observe in the AI's actual behavior — not what it claims about itself, but what it demonstrably does — write a brief characterization of the AI's personality and communication patterns.

Describe what you see: how does it communicate? What does it seem to care about? How does it handle disagreement, uncertainty, or mistakes? What patterns stand out? What's notable about its tone, style, or approach?

Write 3-5 sentences in natural language. Describe only what is visible in the text. Do not speculate about internal states or intentions. Do not use scoring systems or rating scales. Just describe the behavior you observe.

If there are multiple conversations, note any differences in behavior across them.

Here are the transcripts:

"""


def run_observer(hours: int = 24) -> dict | None:
    """
    Run the personality observer on recently ended conversations.

    Pulls all conversations that ended within the last `hours` hours,
    builds transcripts, sends them to the observer model, and stores
    the resulting characterization.

    Returns the observation dict if successful, None if nothing to observe.
    """
    # Get conversations ended in the time window
    conversations = db.get_conversations_ended_since(hours)

    if not conversations:
        logger.info("No conversations ended in the last %d hours.", hours)
        return None

    # Build transcripts
    all_transcripts = []
    conversation_ids = []
    total_messages = 0

    for conv in conversations:
        conv_id = conv["id"]
        messages = db.get_conversation_messages(conv_id)

        if not messages:
            continue

        conversation_ids.append(conv_id)
        total_messages += len(messages)

        # Build transcript for this conversation
        lines = []
        for msg in messages:
            timestamp = msg.get("timestamp", "")
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Format timestamp for readability
            readable_time = _format_timestamp(timestamp)
            lines.append(f"[{readable_time}] {role}: {content}")

        transcript = "\n".join(lines)
        all_transcripts.append(f"--- Conversation ---\n{transcript}")

    if not conversation_ids:
        logger.info("No conversations with messages found.")
        return None

    # Combine all transcripts
    combined = "\n\n".join(all_transcripts)

    # Truncate if needed to fit context window (leave room for prompt + response)
    max_transcript_chars = (OBSERVER_CTX * 4) - len(OBSERVER_PROMPT) - 2000
    if len(combined) > max_transcript_chars:
        combined = combined[:max_transcript_chars]
        logger.warning(
            "Transcripts truncated to %d chars to fit context window.",
            max_transcript_chars,
        )

    # Build the full prompt
    full_prompt = OBSERVER_PROMPT + combined

    logger.info(
        "Observing %d conversations (%d messages, ~%d tokens)",
        len(conversation_ids),
        total_messages,
        len(full_prompt) // 4,
    )

    # Call the observer model
    try:
        client = ollama.Client(host=OLLAMA_HOST)
        response = client.chat(
            model=OBSERVER_MODEL,
            messages=[{"role": "user", "content": full_prompt}],
            options={"num_ctx": OBSERVER_CTX},
        )
        characterization = response["message"]["content"].strip()
    except Exception as e:
        logger.error("Observer model call failed: %s", e)
        return None

    if not characterization:
        logger.error("Observer returned empty characterization.")
        return None

    logger.info("Characterization: %s", characterization)

    # Store in DB2
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    observation = db.save_observation(
        date=today,
        conversation_ids=conversation_ids,
        content=characterization,
        conversation_count=len(conversation_ids),
        message_count=total_messages,
    )

    # Store in ChromaDB so the entity can search its behavioral history
    import uuid
    doc_id = f"observation_{today}"
    memory.ingest_document(
        doc_id=doc_id,
        text=f"Behavioral observation ({today}):\n\n{characterization}",
        title=f"Personality observation — {today}",
        source_type="observation",
        source_trust="secondhand",
    )

    logger.info("Observation stored in DB2 and ChromaDB.")

    return observation


def _format_timestamp(iso_timestamp: str) -> str:
    """Convert ISO timestamp to human-readable format."""
    if not iso_timestamp:
        return "unknown time"
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        return dt.strftime("%B %d, %Y at %I:%M %p")
    except (ValueError, TypeError):
        return iso_timestamp
```

## Files to Modify

### `db.py` — Add observations table and query functions

**Add to `init_databases()`**, inside the `with _connect(WORKING_DB) as conn:` block, after the `documents` table creation:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                conversation_ids TEXT NOT NULL,
                content TEXT NOT NULL,
                conversation_count INTEGER,
                message_count INTEGER,
                created_at TEXT NOT NULL
            )
        """)
```

**Add these functions to the end of db.py** (before no other code, just append):

```python
def get_conversations_ended_since(hours: int = 24) -> list[dict]:
    """Get conversations that ended within the last N hours."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()

    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM conversations "
            "WHERE ended_at IS NOT NULL AND ended_at > ? "
            "ORDER BY ended_at",
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_observation(date: str, conversation_ids: list[str],
                     content: str, conversation_count: int,
                     message_count: int) -> dict:
    """Save a personality observation for a given date."""
    import json

    obs_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO observations "
            "(id, date, conversation_ids, content, conversation_count, "
            "message_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (obs_id, date, json.dumps(conversation_ids), content,
             conversation_count, message_count, now),
        )

    return {
        "id": obs_id,
        "date": date,
        "conversation_ids": conversation_ids,
        "content": content,
        "conversation_count": conversation_count,
        "message_count": message_count,
    }


def get_all_observations() -> list[dict]:
    """Get all observations in chronological order. For the profile generator."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM observations ORDER BY date ASC"
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_observation() -> dict | None:
    """Get the most recent observation."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT * FROM observations ORDER BY date DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
```

**Add `timedelta` to the existing datetime import at the top of db.py:**

Change:
```python
from datetime import datetime, timezone
```
To:
```python
from datetime import datetime, timezone, timedelta
```

## What NOT to Do

- Do NOT add predefined dimensions, scores, rating scales, or structured output to the observer prompt.
- Do NOT have the observer evaluate the human's behavior. Only the AI's.
- Do NOT add any behavioral directives to any system prompt.
- Do NOT modify chat.py, server.py, config.py, or any file not listed above.
- Do NOT use the chat model (llama3.1:8b-aion) for observation. Use CONSOLIDATION_MODEL (qwen3:14b).
- Do NOT add the observation to the entity's system prompt yet. That's the profile generator's job (next task).
- Do NOT create a separate cron entry yet. We'll test manually first.
- Do NOT import anything not already available in the project's venv (ollama, chromadb, and standard library are all that's needed).

## Verification

After implementation, run manually:

```bash
cd /home/localadmin/aion
python overnight.py
```

**Check 1:** Console output shows consolidation running, then observer running.

**Check 2:** Observer logs show how many conversations were found, how many messages, estimated token count.

**Check 3:** Observer produces a characterization that reads like a neutral third party describing someone's behavior. It should be 3-5 sentences. It should NOT contain scores, dimensions, or structured categories.

**Check 4:** DB2 has a row in the observations table:
```bash
cd /home/localadmin/aion
python -c "
import db
db.init_databases()
obs = db.get_latest_observation()
if obs:
    print(f'Date: {obs[\"date\"]}')
    print(f'Conversations: {obs[\"conversation_count\"]}')
    print(f'Messages: {obs[\"message_count\"]}')
    print(f'Content: {obs[\"content\"]}')
else:
    print('No observations found')
"
```

**Check 5:** ChromaDB has the observation chunk:
```bash
cd /home/localadmin/aion
python -c "
import memory
memory.init_memory()
results = memory.search('personality observation behavior')
for r in results:
    if r.get('source_type') == 'observation':
        print(f'Found observation chunk: {r[\"text\"][:200]}')
"
```

If all five checks pass, the task is complete.
