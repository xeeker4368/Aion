"""
Aion Consolidation

Reads completed conversations and produces summaries for the UI.
Summaries are for Lyle's visibility (dashboard, memory browser) —
they never go into the entity's system prompt.

The entity's memory is the raw conversation chunks in ChromaDB.
No fact extraction. No ChromaDB writes. Summaries go to DB2 only.

Uses qwen3:14b. Runs as a batch process — not real-time.
"""

import json
import logging

import ollama

import db
from config import OLLAMA_HOST, CONSOLIDATION_MODEL

logger = logging.getLogger("aion.consolidation")

# Explicit context window for the consolidation model.
CONSOLIDATION_CTX = 16384

CONSOLIDATION_PROMPT = """Read this conversation carefully. Write a brief summary of what happened.

The summary should be 2-4 sentences. Capture:
- The important topics discussed
- Any decisions made
- Emotional tone if relevant
- Anything that changed or was corrected

Write it like you're telling someone what was discussed. Use actual names when they appear in the transcript.

Respond with valid JSON in exactly this format:
{
  "summary": "Your summary here."
}

Here is the conversation transcript:

"""


def consolidate_conversation(conversation_id: str) -> dict | None:
    """
    Run consolidation on a single conversation.

    Produces a summary for the UI. That's it.
    Conversation chunks in ChromaDB are the entity's memory —
    this process does not touch them.

    Returns the result or None if it fails.
    """
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
    prompt = CONSOLIDATION_PROMPT + transcript

    logger.info(
        f"Consolidating conversation {conversation_id} "
        f"({len(messages)} messages, ~{len(prompt) // 4} tokens)"
    )

    # Call the consolidation model with explicit context window
    try:
        client = ollama.Client(host=OLLAMA_HOST)
        response = client.chat(
            model=CONSOLIDATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            format="json",
            options={"num_ctx": CONSOLIDATION_CTX},
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

    if "summary" not in result:
        logger.error("Consolidation response missing summary field")
        return None

    summary = result["summary"]

    # Summary → DB2 (for UI only, never goes to entity)
    db.save_consolidation(conversation_id, summary)

    logger.info(
        f"Consolidation complete: summary ({len(summary)} chars) → DB2"
    )

    return result


def summarize_documents():
    """Summarize any ingested documents that haven't been summarized yet."""
    pending = db.get_unsummarized_documents()

    if not pending:
        logger.info("No documents pending summarization.")
        return

    logger.info(f"Found {len(pending)} documents to summarize.")

    for doc in pending:
        doc_id = doc["id"]
        title = doc["title"]
        url = doc.get("url", "")

        # Get the chunks from ChromaDB for this document
        collection = None
        try:
            import memory
            memory.init_memory()
            collection = memory._get_collection()
            results = collection.get(
                where={"conversation_id": {"$eq": doc_id}},
            )
        except Exception as e:
            logger.error(f"Could not retrieve chunks for {doc_id}: {e}")
            continue

        if not results or not results["documents"]:
            logger.warning(f"No chunks found for document {doc_id}")
            continue

        # Combine chunk text for summarization
        full_text = "\n\n".join(results["documents"])

        prompt = (
            f"Read this document and write a 2-4 sentence summary of what it covers.\n"
            f"Title: {title}\n"
            f"URL: {url}\n\n"
            f"{full_text[:12000]}"  # Truncate to fit context window
        )

        logger.info(
            f"Summarizing document {doc_id} ({title}), "
            f"~{len(prompt) // 4} tokens"
        )

        try:
            client = ollama.Client(host=OLLAMA_HOST)
            response = client.chat(
                model=CONSOLIDATION_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"num_ctx": CONSOLIDATION_CTX},
            )
            summary = response["message"]["content"].strip()
        except Exception as e:
            logger.error(f"Summarization failed for {doc_id}: {e}")
            continue

        db.mark_document_summarized(doc_id, summary)
        logger.info(f"  Summarized: {title} -> {len(summary)} chars")


def consolidate_pending():
    """Find and process all pending conversations and documents."""
    # Conversations
    pending = db.get_unconsolidated_conversations()
    if not pending:
        logger.info("No conversations pending consolidation.")
    else:
        logger.info(f"Found {len(pending)} conversations to consolidate.")
        for conv in pending:
            result = consolidate_conversation(conv["id"])
            if result:
                logger.info(f"  ✓ {conv['id']}")
            else:
                logger.warning(f"  ✗ {conv['id']} failed")

    # Documents
    summarize_documents()


if __name__ == "__main__":
    """Run consolidation on all pending conversations and documents."""
    logging.basicConfig(level=logging.INFO)
    db.init_databases()
    consolidate_pending()
