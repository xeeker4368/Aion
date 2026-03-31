# CC Task 43 — AMENDMENT: Per-Conversation Observations

**This amends the original Task 43 spec. If CC has not started, apply these changes before implementing. If CC has started, apply as a revision.**

## What Changed

The observer produces **one characterization per conversation**, not one per day. This gives the profile generator more granular data — it can see how the entity behaves differently across conversation types.

## Schema Change

Replace the `observations` table with:

```sql
CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    message_count INTEGER,
    created_at TEXT NOT NULL
)
```

Removed: `date`, `conversation_ids` (JSON array), `conversation_count`. These are unnecessary when each observation maps to exactly one conversation.

## db.py Changes

Replace `save_observation` with:

```python
def save_observation(conversation_id: str, content: str,
                     message_count: int) -> dict:
    """Save a personality observation for a single conversation."""
    obs_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO observations "
            "(id, conversation_id, content, message_count, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (obs_id, conversation_id, content, message_count, now),
        )

    return {
        "id": obs_id,
        "conversation_id": conversation_id,
        "content": content,
        "message_count": message_count,
    }
```

Replace `get_all_observations` with:

```python
def get_all_observations() -> list[dict]:
    """Get all observations in chronological order. For the profile generator."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT o.*, c.started_at FROM observations o "
            "JOIN conversations c ON o.conversation_id = c.id "
            "ORDER BY c.started_at ASC"
        ).fetchall()
    return [dict(row) for row in rows]
```

Replace `get_latest_observation` with:

```python
def get_latest_observation() -> dict | None:
    """Get the most recent observation."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT o.*, c.started_at FROM observations o "
            "JOIN conversations c ON o.conversation_id = c.id "
            "ORDER BY c.started_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
```

`get_conversations_ended_since` stays the same — no change needed.

## observer.py Changes

Replace the observer prompt — remove the multi-conversation language:

```python
OBSERVER_PROMPT = """Read the following conversation transcript between a human and an AI. Based on what you observe in the AI's actual behavior — not what it claims about itself, but what it demonstrably does — write a brief characterization of the AI's personality and communication patterns.

Describe what you see: how does it communicate? What does it seem to care about? How does it handle disagreement, uncertainty, or mistakes? What patterns stand out? What's notable about its tone, style, or approach?

Write 3-5 sentences in natural language. Describe only what is visible in the text. Do not speculate about internal states or intentions. Do not use scoring systems or rating scales. Just describe the behavior you observe.

Here is the transcript:

"""
```

Replace `run_observer` — loop over each conversation individually:

```python
def run_observer(hours: int = 24) -> list[dict]:
    """
    Run the personality observer on recently ended conversations.

    Produces one characterization per conversation. Each conversation
    is evaluated independently — the observer sees only that transcript.

    Returns a list of observation dicts, or empty list if nothing to observe.
    """
    conversations = db.get_conversations_ended_since(hours)

    if not conversations:
        logger.info("No conversations ended in the last %d hours.", hours)
        return []

    observations = []

    for conv in conversations:
        conv_id = conv["id"]
        messages = db.get_conversation_messages(conv_id)

        if not messages:
            logger.info("Skipping conversation %s (no messages).", conv_id)
            continue

        # Check if already observed
        existing = db.get_observation_for_conversation(conv_id)
        if existing:
            logger.info("Skipping conversation %s (already observed).", conv_id)
            continue

        # Build transcript
        lines = []
        for msg in messages:
            timestamp = msg.get("timestamp", "")
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            readable_time = _format_timestamp(timestamp)
            lines.append(f"[{readable_time}] {role}: {content}")

        transcript = "\n".join(lines)

        # Truncate if needed
        max_chars = (OBSERVER_CTX * 4) - len(OBSERVER_PROMPT) - 2000
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars]
            logger.warning(
                "Transcript for %s truncated to %d chars.", conv_id, max_chars
            )

        full_prompt = OBSERVER_PROMPT + transcript

        logger.info(
            "Observing conversation %s (%d messages, ~%d tokens)",
            conv_id, len(messages), len(full_prompt) // 4,
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
            logger.error("Observer failed for %s: %s", conv_id, e)
            continue

        if not characterization:
            logger.error("Empty characterization for %s.", conv_id)
            continue

        logger.info("Observation for %s: %s", conv_id, characterization[:150])

        # Store in DB2
        observation = db.save_observation(
            conversation_id=conv_id,
            content=characterization,
            message_count=len(messages),
        )

        # Store in ChromaDB
        doc_id = f"observation_{conv_id}"
        memory.ingest_document(
            doc_id=doc_id,
            text=f"Behavioral observation:\n\n{characterization}",
            title=f"Personality observation",
            source_type="observation",
            source_trust="secondhand",
        )

        observations.append(observation)

    logger.info("Observer complete: %d conversations observed.", len(observations))
    return observations
```

## Additional db.py Function

Add this function (needed by the observer to skip already-observed conversations):

```python
def get_observation_for_conversation(conversation_id: str) -> dict | None:
    """Check if a conversation has already been observed."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT * FROM observations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None
```

## overnight.py Change

Update the observer result handling since `run_observer` now returns a list:

```python
    # Step 2: Personality observer
    logger.info("--- Step 2: Personality Observer ---")
    try:
        results = run_observer()
        if results:
            for obs in results:
                logger.info(
                    f"  Observed conversation {obs['conversation_id']}: "
                    f"{obs['message_count']} messages"
                )
            logger.info(f"Observer: {len(results)} conversations characterized.")
        else:
            logger.info("Observer: nothing to observe.")
    except Exception as e:
        logger.error(f"Observer failed: {e}")
```

## Verification Changes

**Check 4** now lists individual observations:

```bash
cd /home/localadmin/aion
python -c "
import db
db.init_databases()
obs = db.get_all_observations()
print(f'Total observations: {len(obs)}')
for o in obs:
    print(f'  Conv {o[\"conversation_id\"][:8]}... ({o[\"message_count\"]} msgs)')
    print(f'    {o[\"content\"][:120]}')
    print()
"
```

## What NOT to Do (additions)

- Do NOT batch multiple conversations into one observer prompt. Each conversation is observed independently.
- Do NOT skip short conversations (even a 2-message exchange reveals behavior).
- Do NOT re-observe a conversation that already has an observation (check first).
