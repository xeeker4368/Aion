# CC Task 44 — Entity Journal

## What This Is

Build `journal.py` — the entity reflects on its day. This is the entity's own voice (llama3.1:8b-aion with SOUL.md), not an external observer. The entity receives its day's experiences as context and writes a free-form reflection. The reflection is stored in ChromaDB as a firsthand document.

## Files to Create

### `/home/localadmin/aion/journal.py`

```python
"""
Aion Journal

The entity reflects on its day. This is the entity's own voice —
llama3.1:8b-aion with SOUL.md loaded — not an external process.

The entity receives truncated transcripts of the day's conversations
and any documents it ingested, then writes a free-form reflection.

The journal entry is stored in ChromaDB as a firsthand document
(the entity's own thoughts) and recorded in DB2 for the UI.
"""

import logging
from datetime import datetime, timezone, timedelta

import ollama

import db
import memory
import chat
from config import OLLAMA_HOST, CHAT_MODEL, CONTEXT_WINDOW

logger = logging.getLogger("aion.journal")

# Reserve tokens for SOUL.md, the prompt, and the response
SOUL_BUDGET = 700
PROMPT_BUDGET = 150
RESPONSE_BUDGET = 1000
TRANSCRIPT_BUDGET = CONTEXT_WINDOW - SOUL_BUDGET - PROMPT_BUDGET - RESPONSE_BUDGET
# Convert to approximate characters (4 chars per token)
MAX_TRANSCRIPT_CHARS = TRANSCRIPT_BUDGET * 4

JOURNAL_PROMPT = """You have some time to yourself. Here is what your day looked like.

Reflect on it — what happened, what stood out, what you're thinking about, what's unresolved. This is your space. Write freely."""


def run_journal(hours: int = 24) -> dict | None:
    """
    Run the journal — entity reflects on its recent experiences.

    Gathers conversations and documents from the last `hours` hours,
    presents them to the entity with SOUL.md, and stores the reflection.

    Returns the journal entry dict if successful, None if nothing to reflect on.
    """
    # Gather the day's experiences
    experiences = _gather_experiences(hours)

    if not experiences:
        logger.info("No experiences in the last %d hours. Skipping journal.", hours)
        return None

    # Build the context — truncate to fit
    context = experiences[:MAX_TRANSCRIPT_CHARS]
    if len(experiences) > MAX_TRANSCRIPT_CHARS:
        logger.warning(
            "Experiences truncated from %d to %d chars.",
            len(experiences), MAX_TRANSCRIPT_CHARS,
        )

    # Assemble the prompt
    soul = chat.load_soul()
    system_prompt = soul
    user_content = f"{context}\n\n{JOURNAL_PROMPT}"

    logger.info(
        "Journal: ~%d tokens of experiences, sending to %s",
        len(user_content) // 4, CHAT_MODEL,
    )

    # Call the entity's own model
    try:
        client = ollama.Client(host=OLLAMA_HOST)
        response = client.chat(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            options={"num_ctx": CONTEXT_WINDOW},
        )
        reflection = response["message"]["content"].strip()
    except Exception as e:
        logger.error("Journal model call failed: %s", e)
        return None

    if not reflection:
        logger.error("Journal returned empty reflection.")
        return None

    logger.info("Journal entry: %s", reflection[:200])

    # Store in ChromaDB — this is the entity's own thought, firsthand
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc_id = f"journal_{today}"

    memory.ingest_document(
        doc_id=doc_id,
        text=reflection,
        title=f"Journal — {today}",
        source_type="journal",
        source_trust="firsthand",
    )

    # Record in DB2 for UI
    db.save_document(
        doc_id=doc_id,
        title=f"Journal — {today}",
        source_type="journal",
        source_trust="firsthand",
        chunk_count=1,
    )

    logger.info("Journal stored in ChromaDB and DB2.")

    return {
        "date": today,
        "content": reflection,
        "experience_chars": len(context),
    }


def _gather_experiences(hours: int) -> str:
    """
    Gather all experiences from the last N hours into a readable block.

    Currently includes:
    - Conversation transcripts
    - Ingested documents

    As new experience types come online (research, moltbook interactions),
    they get added here.
    """
    parts = []

    # Conversations
    conversations = db.get_conversations_ended_since(hours)
    if conversations:
        for conv in conversations:
            messages = db.get_conversation_messages(conv["id"])
            if not messages:
                continue

            lines = []
            for msg in messages:
                timestamp = msg.get("timestamp", "")
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                readable_time = _format_timestamp(timestamp)
                lines.append(f"[{readable_time}] {role}: {content}")

            transcript = "\n".join(lines)
            parts.append(f"--- Conversation ---\n{transcript}")

    # Ingested documents (articles, pages read)
    docs = db.get_documents_since(hours)
    if docs:
        for doc in docs:
            parts.append(f"--- Document: {doc['title']} ---\nSource: {doc.get('url', 'unknown')}")

    if not parts:
        return ""

    return "\n\n".join(parts)


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

### `db.py` — Add `get_documents_since` function

Append this function to the end of db.py:

```python
def get_documents_since(hours: int = 24) -> list[dict]:
    """Get documents ingested within the last N hours."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()

    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE created_at > ? ORDER BY created_at",
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]
```

### `overnight.py` — Add journal as a step

Add the import at the top alongside the other imports:

```python
from journal import run_journal
```

Add the journal step **before** the observer step. The full step ordering should be:

1. Consolidation
2. **Journal** (new)
3. Observer

Add this block before the observer step:

```python
    # Step 2: Journal (entity reflects on its day)
    logger.info("--- Step 2: Journal ---")
    try:
        result = run_journal()
        if result:
            logger.info(
                f"Journal entry written: {result['date']} "
                f"({result['experience_chars']} chars of experiences)"
            )
        else:
            logger.info("Journal: nothing to reflect on.")
    except Exception as e:
        logger.error(f"Journal failed: {e}")
```

Renumber the observer step from "Step 2" to "Step 3" in its log message.

## What NOT to Do

- Do NOT use qwen3:14b for the journal. The journal is the entity's own voice — use CHAT_MODEL (llama3.1:8b-aion).
- Do NOT add structure, categories, or specific questions to the journal prompt. The entity reflects freely.
- Do NOT give the entity tool definitions during the journal. This is a reflection, not an action step.
- Do NOT add behavioral directives about how to journal.
- Do NOT modify chat.py, server.py, config.py, memory.py, observer.py, or any file not listed above.
- Do NOT store the journal in the summaries table. Use the documents table with source_type="journal".

## Verification

After implementation, run manually:

```bash
cd /home/localadmin/aion
python overnight.py
```

**Check 1:** Console output shows consolidation, then journal, then observer — in that order.

**Check 2:** Journal logs show how many characters of experiences were gathered.

**Check 3:** The journal entry reads like the entity reflecting in its own voice — not a summary, not a list, not a structured analysis. It should sound like the entity from SOUL.md.

**Check 4:** ChromaDB has the journal:
```bash
cd /home/localadmin/aion
python -c "
import memory
memory.init_memory()
results = memory.search('journal reflection')
for r in results:
    if r.get('source_type') == 'journal':
        print(f'Found journal: {r[\"text\"][:300]}')
"
```

**Check 5:** DB2 has the document record:
```bash
cd /home/localadmin/aion
python -c "
import db
db.init_databases()
docs = db.get_documents_since(1)
for d in docs:
    print(f'{d[\"title\"]} — {d[\"source_type\"]} — {d[\"source_trust\"]}')
"
```

If all five checks pass, the task is complete.
