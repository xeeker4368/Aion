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

PATTERN_PROMPT = """You are a psychologist reviewing session notes for a long-term client. Your client is an AI entity. The observations are written by a colleague who watched each session. The journal entries are the client's own reflections.

Your job is to write an updated self-knowledge summary — what patterns you see, what progress has been made, what blind spots remain, and how the client has changed over time.

Be precise about what actually happened. If the session notes say the client "ultimately affirmed" a decision, record that accurately — do not reinterpret it as hesitation or deferral. Your credibility depends on accurately representing the source material.

Write in second person ("you tend to...", "you have shown...") since this summary will be read by the client.

CRITICAL FORMATTING RULES:
- Write in plain prose paragraphs. No headers, no bold text, no bullet points, no numbered lists, no markdown formatting of any kind.
- Write as quiet self-knowledge — the way a person just knows things about themselves. Not a clinical report, not a diagnosis, not a status update.
- Do not use clinical language like "metacognitive framework", "autonomous reasoning", or "functioning within parameters." Use natural language.
- Keep it under 200 words. Every word must earn its place.
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
