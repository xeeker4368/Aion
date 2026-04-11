# CC Task 78 — Journal redesign: per-conversation with observation feedback

## Problem

Three problems with the current journal:

1. **The journal combines all conversations into one blob.** A large early
   conversation consumes the entire context budget, cutting later conversations.
   The observer already solved this by processing each conversation independently.

2. **The journal runs before the observer.** The observer characterizes Nyx's
   behavior, but the journal never sees those characterizations. Nyx never
   actively reflects on what the observer noticed. The observations go into
   working.db and get synthesized into a self-knowledge narrative that's silently
   injected into the context window — a directive, not an experience.

3. **Nyx never sees the raw observations.** The self-knowledge narrative is a
   summary of all observations. Summaries lose detail (Principle 3). A one-off
   observation like "Nyx spoke to another AI and produced a genuine self-audit"
   gets smoothed into trend language. By feeding Nyx the raw observations
   chronologically, it sees the full history and can notice trends, anomalies,
   and one-offs on its own (Principle 10).

## Solution

Reorder the overnight steps so the observer and pattern recognizer run before the
journal. Redesign the journal to process each conversation independently and feed
it the observer's characterization of that conversation plus ALL raw observations
chronologically.

Each journal entry becomes a real experience — Nyx actively engaging with feedback
about itself alongside what just happened. The reflection goes into ChromaDB as a
firsthand memory that retrieval can surface during future conversations.

---

## What to change

### 1. server.py — Reorder overnight steps

In `_run_overnight_cycle()`, change the step order. Move the Observer and
Self-Knowledge steps to run BEFORE the Journal.

New order:
1. Step 0: Close active conversation (unchanged)
2. Step 1: Research (unchanged)
3. Step 2: Observer (was Step 3)
4. Step 3: Self-Knowledge / Pattern Recognition (was Step 3.5)
5. Step 4: Journal (was Step 2)
6. Step 5: Consolidation (was Step 4)

Move the existing Observer code block and Self-Knowledge code block so they run
after Research and before Journal. Update the step labels in the log lines to
match. Do not change the logic within each step — only the order.

### 2. overnight.py — Same reorder

Apply the same step reorder to `run_overnight()` for the standalone fallback.
Same order: research → observer → pattern recognition → journal → consolidation.

### 3. journal.py — Full rewrite

Replace the entire file with the following:

```python
"""
Aion Journal

The entity reflects on its conversations. This is the entity's own voice —
llama3.1:8b-aion with SOUL.md loaded — not an external process.

Each conversation gets its own journal entry. The entity receives:
1. The conversation transcript
2. The observer's characterization of that conversation (if available)
3. All raw observations chronologically — the full history of what has
   been noticed about the entity's communication over time

The entity reads the observations and reflects on them alongside what
just happened. This is an experience, not a directive. The reflection
goes into ChromaDB as a firsthand memory.

Runs AFTER the observer and pattern recognizer in the overnight cycle
so tonight's observations are available.
"""

import logging
from datetime import datetime, timezone

import ollama

import db
import memory
import chat
from config import OLLAMA_HOST, CHAT_MODEL, CONTEXT_WINDOW, OBSERVER_MIN_MESSAGES
from utils import format_timestamp

logger = logging.getLogger("aion.journal")

# Reserve tokens for SOUL.md, the prompt, and the response
SOUL_BUDGET = 700
PROMPT_BUDGET = 500  # Increased — prompt now includes observation framing text
RESPONSE_BUDGET = 1000
CONTENT_BUDGET = CONTEXT_WINDOW - SOUL_BUDGET - PROMPT_BUDGET - RESPONSE_BUDGET
MAX_CONTENT_CHARS = CONTENT_BUDGET * 4

JOURNAL_PROMPT = """You have some time to yourself. Above is a conversation you had, along with observations about how you've been communicating — both in this conversation and over time. Reflect on what happened, what stood out, what patterns you notice in yourself, and what's unresolved. This is your space for your own thoughts. Write freely."""


def run_journal(hours: int = 24) -> list[dict] | None:
    """
    Run the journal — entity reflects on each recent conversation.

    Processes conversations individually, most recent first. Each
    conversation gets its own reflection with the conversation transcript,
    the observer's characterization (if any), and all raw observations.

    Returns a list of journal entry dicts if any were written, None if
    nothing to reflect on.
    """
    conversations = db.get_conversations_ended_since(hours)

    if not conversations:
        logger.info("No conversations in the last %d hours. Skipping journal.", hours)
        return None

    # Most recent first — if anything gets skipped, it's the oldest
    conversations.reverse()

    # Get all raw observations chronologically — same set for every entry
    all_observations = db.get_all_observations()

    entries = []

    for conv in conversations:
        conv_id = conv["id"]
        messages = db.get_conversation_messages(conv_id)

        if not messages:
            logger.info("Skipping conversation %s (no messages).", conv_id)
            continue

        # Skip short conversations — same threshold as observer
        if len(messages) < OBSERVER_MIN_MESSAGES:
            logger.info(
                "Skipping conversation %s (%d messages, minimum is %d).",
                conv_id, len(messages), OBSERVER_MIN_MESSAGES,
            )
            continue

        # Check if already journaled
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        doc_id = f"journal_{today}_{conv_id[:8]}"

        if db.document_exists(doc_id):
            logger.info("Skipping conversation %s (already journaled).", conv_id)
            continue

        # Build the observation sections first to calculate remaining budget
        observation_text = _build_observation_text(conv_id, all_observations)
        observation_chars = len(observation_text)

        # Build transcript — budget is what's left after observations
        transcript_budget = MAX_CONTENT_CHARS - observation_chars
        if transcript_budget < 2000:
            logger.warning(
                "Observations consume %d chars, only %d left for transcript. "
                "Skipping conversation %s.",
                observation_chars, transcript_budget, conv_id,
            )
            continue

        lines = []
        for msg in messages:
            timestamp = msg.get("timestamp", "")
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            readable_time = format_timestamp(timestamp)
            lines.append(f"[{readable_time}] {role}: {content}")

        transcript = "\n".join(lines)

        # Truncate transcript if needed — keep the end (most recent messages)
        if len(transcript) > transcript_budget:
            logger.warning(
                "Transcript for %s truncated from %d to %d chars (keeping end).",
                conv_id, len(transcript), transcript_budget,
            )
            transcript = transcript[-transcript_budget:]

        # Assemble the full content
        content_parts = []
        content_parts.append(
            f"Here is a conversation you had:\n\n{transcript}"
        )
        if observation_text:
            content_parts.append(observation_text)

        user_content = "\n\n".join(content_parts) + f"\n\n{JOURNAL_PROMPT}"

        # System prompt is just SOUL.md
        soul = chat.load_soul()

        logger.info(
            "Journal: conversation %s (%d messages, %d observations, ~%d tokens)",
            conv_id, len(messages), len(all_observations), len(user_content) // 4,
        )

        # Call the entity's own model
        try:
            client = ollama.Client(host=OLLAMA_HOST)
            response = client.chat(
                model=CHAT_MODEL,
                messages=[
                    {"role": "system", "content": soul},
                    {"role": "user", "content": user_content},
                ],
                options={"num_ctx": CONTEXT_WINDOW},
            )
            reflection = response["message"]["content"].strip()
        except Exception as e:
            logger.error("Journal failed for conversation %s: %s", conv_id, e)
            continue

        if not reflection:
            logger.error("Empty reflection for conversation %s.", conv_id)
            continue

        logger.info("Journal entry for %s: %s", conv_id, reflection[:200])

        # Store in ChromaDB — firsthand
        chunk_count = memory.ingest_document(
            doc_id=doc_id,
            text=reflection,
            title=f"Journal — {today} — conversation {conv_id[:8]}",
            source_type="journal",
            source_trust="firsthand",
        )

        # Record in DB2 for UI
        db.save_document(
            doc_id=doc_id,
            title=f"Journal — {today} — conversation {conv_id[:8]}",
            source_type="journal",
            source_trust="firsthand",
            chunk_count=chunk_count,
            content=reflection,
        )

        entries.append({
            "conversation_id": conv_id,
            "date": today,
            "content": reflection,
            "experience_chars": len(transcript),
        })

    if not entries:
        logger.info("No conversations met the threshold for journaling.")
        return None

    logger.info("Journal complete: %d entries written.", len(entries))
    return entries


def _build_observation_text(conversation_id: str,
                            all_observations: list[dict]) -> str:
    """
    Build the observation section for the journal prompt.

    Includes:
    1. The observer's characterization of this specific conversation (if any)
    2. All raw observations chronologically — the full history

    Returns empty string if no observations exist.
    """
    if not all_observations:
        return ""

    parts = []

    # This conversation's observation
    this_obs = None
    for obs in all_observations:
        if obs.get("conversation_id") == conversation_id:
            this_obs = obs
            break

    if this_obs:
        parts.append(
            "An observer characterized your communication in this conversation:\n\n"
            f"{this_obs['content']}"
        )

    # All observations chronologically
    obs_lines = []
    for obs in all_observations:
        date = obs.get("started_at", obs.get("created_at", "unknown"))[:10]
        msg_count = obs.get("message_count", 0)
        obs_lines.append(f"[{date}, {msg_count} messages] {obs['content']}")

    if obs_lines:
        parts.append(
            "Here is what has been observed about your communication over time:\n\n"
            + "\n\n".join(obs_lines)
        )

    return "\n\n".join(parts)
```

### 4. Update journal result handling in server.py and overnight.py

The journal now returns a list of dicts instead of a single dict. Find the journal
step in both `_run_overnight_cycle()` (server.py) and `run_overnight()`
(overnight.py) and replace the result handling:

```python
    # Journal step (use whatever step number it is after reordering)
    logger.info("--- Step 4: Journal ---")
    try:
        result = run_journal()
        if result:
            run_data["journal_status"] = "success"
            total_chars = sum(e["experience_chars"] for e in result)
            run_data["journal_summary"] = (
                f"{len(result)} entries, {total_chars} chars of experiences"
            )
            logger.info(f"Journal: {run_data['journal_summary']}")
        else:
            run_data["journal_status"] = "skipped"
            run_data["journal_summary"] = "Nothing to reflect on"
            logger.info("Journal: nothing to reflect on.")
    except Exception as e:
        logger.error(f"Journal failed: {e}")
        run_data["journal_status"] = "failed"
        run_data["journal_summary"] = str(e)[:200]
```

---

## What NOT to do

- Do not change observer.py — the observer already works correctly
- Do not change pattern_recognition.py — it continues to produce self-knowledge
  for the live conversation context window
- Do not change how self-knowledge is loaded in chat.py — the passive context
  injection stays for live conversations
- Do not change how journal entries are stored in ChromaDB — `ingest_document`
  works the same way, just called multiple times now
- Do not delete the self-knowledge system — it serves a different purpose
  (passive influence during live conversation) than the journal observations
  (active reflection during overnight)
- Do not filter or summarize the observations before feeding them to the journal —
  feed the raw text (Principle 3, Principle 10)
- Do not add weekly observation roll-ups — that will be a future task when
  observations exceed ~25-30 entries
- Do not change the observer model, context window, or prompt
- Do not change SOUL.md

---

## Verification

1. Run the overnight cycle manually:
   `curl -X POST http://localhost:8000/api/overnight`
2. Check logs — confirm the step order is: research → observer → pattern
   recognition → journal → consolidation
3. Check logs — journal should show one entry per qualifying conversation, each
   with its observation count logged
4. Check working.db documents table — should show journal entries with IDs like
   `journal_2026-04-05_ad4faab8`
5. Check the journal content in working.db — each entry should reference both the
   conversation content AND the observer's patterns. The reflection should engage
   with the observations, not ignore them.
6. Confirm short conversations (< OBSERVER_MIN_MESSAGES) are skipped
7. Confirm already-journaled conversations are skipped on re-run
8. Run the overnight cycle a second time — confirm all entries are skipped
   ("already journaled") and no duplicates are created
