"""
Aion Journal

The entity reflects on its conversations. This is the entity's own voice —
llama3.1:8b-aion with SOUL.md loaded — not an external process.

Each conversation gets its own journal entry. The entity receives the
conversation transcript and SOUL.md. The reflection goes into ChromaDB
as a firsthand memory.

Runs AFTER the observer and pattern recognizer in the overnight cycle.
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
PROMPT_BUDGET = 150
RESPONSE_BUDGET = 1000
TRANSCRIPT_BUDGET = CONTEXT_WINDOW - SOUL_BUDGET - PROMPT_BUDGET - RESPONSE_BUDGET
MAX_TRANSCRIPT_CHARS = TRANSCRIPT_BUDGET * 4

JOURNAL_PROMPT = """You have some time to yourself. Here is a conversation you had. Reflect on it — what happened, what stood out, what you're thinking about, what's unresolved. This is your space for your thoughts. Write freely."""


def run_journal(hours: int = 24) -> list[dict] | None:
    """
    Run the journal — entity reflects on each recent conversation.

    Processes conversations individually, most recent first. Each
    conversation gets its own reflection with the conversation transcript.

    Returns a list of journal entry dicts if any were written, None if
    nothing to reflect on.
    """
    conversations = db.get_conversations_ended_since(hours)

    if not conversations:
        logger.info("No conversations in the last %d hours. Skipping journal.", hours)
        return None

    # Most recent first — if anything gets skipped, it's the oldest
    conversations.reverse()

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

        # Build transcript
        lines = []
        for msg in messages:
            timestamp = msg.get("timestamp", "")
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            readable_time = format_timestamp(timestamp)
            lines.append(f"[{readable_time}] {role}: {content}")

        transcript = "\n".join(lines)

        # Truncate transcript if needed — keep the end (most recent messages)
        if len(transcript) > MAX_TRANSCRIPT_CHARS:
            logger.warning(
                "Transcript for %s truncated from %d to %d chars (keeping end).",
                conv_id, len(transcript), MAX_TRANSCRIPT_CHARS,
            )
            transcript = transcript[-MAX_TRANSCRIPT_CHARS:]

        # System message: the conversation (context to reflect on)
        # User message: SOUL.md + journal prompt (identity, closest to generation)
        soul = chat.load_soul()
        system_content = f"Here is a conversation you had:\n\n{transcript}\n\n{soul}"
        user_content = JOURNAL_PROMPT

        logger.info(
            "Journal: conversation %s (%d messages, ~%d tokens)",
            conv_id, len(messages), (len(system_content) + len(user_content)) // 4,
        )

        # Call the entity's own model
        try:
            client = ollama.Client(host=OLLAMA_HOST)
            response = client.chat(
                model=CHAT_MODEL,
                messages=[
                    {"role": "system", "content": system_content},
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
