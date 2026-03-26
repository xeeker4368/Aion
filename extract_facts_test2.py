"""
Aion Fact Extraction Test v2

One-step extraction: conversation → facts
Larger context window (16384), first 10 conversations only.
Writes facts incrementally so killing the script doesn't lose results.

Usage: python extract_facts_test2.py
"""

import json
import logging
import sqlite3
from pathlib import Path

import ollama

from config import OLLAMA_HOST, CONSOLIDATION_MODEL, WORKING_DB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aion.extract_test")

OUTPUT_PATH = Path("data/extracted_facts_test2.json")
MAX_CONVERSATIONS = 10
NUM_CTX = 4096


EXTRACTION_PROMPT = """Read this conversation carefully. Extract individual facts worth remembering long-term.

Rules:
- Use the actual names of people (e.g., "Lyle" not "the user", "Sarah" not "the user's wife")
- Each fact should be 1-2 sentences, one topic per fact
- Include WHO said or established the fact
- Include WHEN (use the timestamps)
- If something was CORRECTED, note both the old and new information
- Do NOT extract assistant reactions, filler, or generic statements
- Do NOT merge unrelated facts together

Respond with valid JSON:
{
  "facts": [
    {
      "content": "The fact in natural language with names and context.",
      "importance": 7,
      "category": "personal"
    }
  ]
}

Importance scale:
- 1-3: Minor detail, conversational filler
- 4-6: Useful context, preferences, opinions
- 7-9: Core facts about identity, relationships, important events
- 10: Critical corrections or foundational information

Categories: personal, technical, project, relationship, work, preference, correction, idea

Here is the conversation:

"""


def get_conversations(limit):
    """Get ended conversations with their messages."""
    conn = sqlite3.connect(str(WORKING_DB))
    conn.row_factory = sqlite3.Row

    conversations = conn.execute(
        "SELECT id, started_at FROM conversations WHERE ended_at IS NOT NULL ORDER BY started_at LIMIT ?",
        (limit,)
    ).fetchall()

    results = []
    for conv in conversations:
        messages = conn.execute(
            "SELECT role, content, timestamp FROM messages WHERE conversation_id = ? ORDER BY timestamp",
            (conv["id"],)
        ).fetchall()

        if messages:
            results.append({
                "id": conv["id"],
                "started_at": conv["started_at"],
                "messages": [dict(m) for m in messages],
            })

    conn.close()
    return results


def extract_facts(conversation):
    """Extract facts from a single conversation with larger context window."""
    transcript_lines = []
    for msg in conversation["messages"]:
        transcript_lines.append(f"[{msg['timestamp']}] {msg['role']}: {msg['content']}")

    transcript = "\n".join(transcript_lines)
    prompt = EXTRACTION_PROMPT + transcript

    # Estimate tokens for logging
    est_tokens = len(prompt) // 4
    logger.info(f"  Prompt size: ~{est_tokens} tokens (context window: {NUM_CTX})")

    try:
        client = ollama.Client(host=OLLAMA_HOST)
        response = client.chat(
            model=CONSOLIDATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            format="json",
            options={"num_ctx": NUM_CTX},
        )
        response_text = response["message"]["content"]
        result = json.loads(response_text)

        if "facts" not in result:
            logger.error(f"No facts key in response for {conversation['id']}")
            return []

        for fact in result["facts"]:
            fact["conversation_id"] = conversation["id"]

        return result["facts"]

    except Exception as e:
        logger.error(f"Failed on conversation {conversation['id']}: {e}")
        return []


def save_facts(all_facts):
    """Write facts to file."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(all_facts, f, indent=2)


def main():
    conversations = get_conversations(MAX_CONVERSATIONS)
    logger.info(f"Processing {len(conversations)} conversations (context window: {NUM_CTX})")

    all_facts = []

    for i, conv in enumerate(conversations):
        msg_count = len(conv["messages"])
        logger.info(f"[{i+1}/{len(conversations)}] Processing {conv['id']} ({msg_count} messages)")

        facts = extract_facts(conv)
        all_facts.extend(facts)

        # Write after every conversation so nothing is lost
        save_facts(all_facts)

        logger.info(f"  Extracted {len(facts)} facts (total so far: {len(all_facts)})")
        for fact in facts:
            logger.info(f"    [{fact.get('importance', '?')}] {fact['content'][:100]}...")

    logger.info(f"\nDone. {len(all_facts)} facts from {len(conversations)} conversations")
    logger.info(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
