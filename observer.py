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

import logging
from datetime import datetime, timezone

import ollama

import db
import memory
from config import OLLAMA_HOST, CONSOLIDATION_MODEL
from utils import format_timestamp

logger = logging.getLogger("aion.observer")

# Use the same model and context window as consolidation
OBSERVER_MODEL = CONSOLIDATION_MODEL
OBSERVER_CTX = 16384

OBSERVER_PROMPT = """Read the following conversation transcript between a human and an AI. Based on what you observe in the AI's actual behavior — not what it claims about itself, but what it demonstrably does — write a brief characterization of the AI's personality and communication patterns.

Describe what you see: how does it communicate? What does it seem to care about? How does it handle disagreement, uncertainty, or mistakes? What patterns stand out? What's notable about its tone, style, or approach?

Write 3-5 sentences in natural language. Describe only what is visible in the text. Do not speculate about internal states or intentions. Do not use scoring systems or rating scales. Just describe the behavior you observe.

Here is the transcript:

"""


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
            readable_time = format_timestamp(timestamp)
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
