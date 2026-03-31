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
from datetime import datetime, timezone

import ollama

import db
import memory
import chat
from config import OLLAMA_HOST, CHAT_MODEL, CONTEXT_WINDOW
from utils import format_timestamp

logger = logging.getLogger("aion.journal")

# Reserve tokens for SOUL.md, the prompt, and the response
SOUL_BUDGET = 700
PROMPT_BUDGET = 150
RESPONSE_BUDGET = 1000
TRANSCRIPT_BUDGET = CONTEXT_WINDOW - SOUL_BUDGET - PROMPT_BUDGET - RESPONSE_BUDGET
# Convert to approximate characters (4 chars per token)
MAX_TRANSCRIPT_CHARS = TRANSCRIPT_BUDGET * 4

JOURNAL_PROMPT = """You have some time to yourself. Here is what your day looked like. Reflect on it — what happened, what stood out, what you're thinking about, what's unresolved. This is your space for your thoughts. Write freely."""


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

    chunk_count = memory.ingest_document(
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
        chunk_count=chunk_count,
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
                readable_time = format_timestamp(timestamp)
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
