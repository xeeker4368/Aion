"""
Aion Consolidation

Reads completed conversations and produces two things:
- A summary: natural language account of what happened
- Facts: individual pieces of knowledge worth remembering

Uses qwen3:14b for higher quality output. Runs as a background
process — not real-time, so speed doesn't matter.

One prompt, one pass. No structured extraction pipeline.
The model reads the conversation and reasons about it naturally.
(Principle 7: the model is smart, stop fighting it.)
"""

import json
import logging

import ollama

import db
import memory
from config import OLLAMA_HOST, CONSOLIDATION_MODEL

logger = logging.getLogger("aion.consolidation")

CONSOLIDATION_PROMPT = """You are reading a conversation transcript. Your job is to extract what matters from this conversation for long-term memory.

Read the full conversation carefully, then produce two things:

1. SUMMARY: A natural language account of what happened in this conversation. Write it like you're telling someone what was discussed. 2-4 sentences. Capture the important topics, any decisions made, emotional tone if relevant, and anything that changed or was corrected.

2. FACTS: Individual pieces of knowledge worth remembering long-term. Each fact should be:
- 1-3 sentences, one topic per fact
- Written in natural language with context (who said it, why it matters)
- Given an importance score from 1-10:
  - 1-3: Minor detail, conversational filler
  - 4-6: Useful context, preferences, opinions
  - 7-9: Core facts about identity, relationships, important events
  - 10: Critical corrections or foundational information

Rules for facts:
- Include WHO said or established the fact
- Include WHEN (use the timestamps from the conversation)
- Do NOT include assistant reactions or conversational filler as facts
- Do NOT merge unrelated facts into one entry
- If something was CORRECTED in this conversation, note both the old and new information
- If a fact references something from a previous conversation, note that connection

Respond with valid JSON in exactly this format:
{
  "summary": "Your summary here.",
  "facts": [
    {
      "content": "The fact in natural language with context.",
      "importance": 7,
      "category": "personal"
    }
  ]
}

Categories: personal, technical, project, relationship, work, preference, correction, idea

Here is the conversation transcript:

"""


def consolidate_conversation(conversation_id: str) -> dict | None:
    """
    Run consolidation on a single conversation.
    Returns the consolidation result or None if it fails.
    """
    # Get the conversation messages
    messages = db.get_conversation_messages(conversation_id)
    if not messages:
        logger.warning(f"No messages found for conversation {conversation_id}")
        return None

    # Build the transcript
    transcript_lines = []
    for msg in messages:
        timestamp = msg.get("timestamp", "")
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        transcript_lines.append(f"[{timestamp}] {role}: {content}")

    transcript = "\n".join(transcript_lines)

    # Call qwen3:14b
    prompt = CONSOLIDATION_PROMPT + transcript

    logger.info(f"Consolidating conversation {conversation_id} ({len(messages)} messages)")

    try:
        client = ollama.Client(host=OLLAMA_HOST)
        response = client.chat(
            model=CONSOLIDATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            format="json",
        )
        response_text = response["message"]["content"]
    except Exception as e:
        logger.error(f"Ollama call failed: {e}")
        return None

    # Parse the response
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse consolidation response: {e}")
        logger.error(f"Raw response: {response_text[:500]}")
        return None

    # Validate structure
    if "summary" not in result or "facts" not in result:
        logger.error(f"Consolidation response missing required fields")
        return None

    # Store the results
    summary = result["summary"]
    facts = result["facts"]

    db.save_consolidation(conversation_id, summary, facts)

    # Embed facts into ChromaDB for retrieval
    _embed_facts(conversation_id, facts)

    logger.info(
        f"Consolidation complete: {len(summary)} char summary, {len(facts)} facts"
    )

    return result


def _embed_facts(conversation_id: str, facts: list[dict]):
    """Embed extracted facts into ChromaDB for retrieval."""
    if not facts:
        return

    collection = memory._get_collection()

    ids = []
    documents = []
    metadatas = []

    for i, fact in enumerate(facts):
        fact_id = f"{conversation_id}_fact_{i}"
        content = fact.get("content", "")
        importance = fact.get("importance", 5)
        category = fact.get("category", "general")

        if not content:
            continue

        ids.append(fact_id)
        documents.append(content)
        metadatas.append({
            "conversation_id": conversation_id,
            "type": "fact",
            "importance": importance,
            "category": category,
        })

    if ids:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)


def consolidate_pending():
    """Find and consolidate all conversations that need it."""
    pending = db.get_unconsolidated_conversations()

    if not pending:
        logger.info("No conversations pending consolidation.")
        return

    logger.info(f"Found {len(pending)} conversations to consolidate.")

    for conv in pending:
        result = consolidate_conversation(conv["id"])
        if result:
            logger.info(f"  ✓ {conv['id']}")
        else:
            logger.warning(f"  ✗ {conv['id']} failed")


if __name__ == "__main__":
    """Run consolidation on all pending conversations."""
    logging.basicConfig(level=logging.INFO)
    db.init_databases()
    memory.init_memory()
    consolidate_pending()
