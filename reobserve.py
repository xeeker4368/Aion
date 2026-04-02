"""
One-off: Re-observe all conversations with the new prompt.

Clears existing observations from both working.db and ChromaDB,
then re-runs the observer on every ended conversation with 6+ messages.

Usage:
    Production:  python reobserve.py
    Dev mode:    python reobserve.py --dev

Delete this script after running.
"""

import logging
import sqlite3

import ollama

import db
import memory
from config import WORKING_DB, OLLAMA_HOST, OBSERVER_MIN_MESSAGES
from observer import OBSERVER_MODEL, OBSERVER_CTX, OBSERVER_PROMPT
from utils import format_timestamp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
logger = logging.getLogger("aion.reobserve")


def main():
    db.init_databases()
    memory.init_memory()

    # Step 1: Clear existing observations from working.db
    conn = sqlite3.connect(str(WORKING_DB))
    old_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    conn.execute("DELETE FROM observations")
    conn.commit()
    conn.close()
    logger.info(f"Cleared {old_count} observations from working.db")

    # Step 2: Clear observation chunks from ChromaDB
    collection = memory._get_collection()
    try:
        results = collection.get(
            where={"source_type": "observation"},
            include=[],
        )
        if results and results["ids"]:
            collection.delete(ids=results["ids"])
            logger.info(f"Cleared {len(results['ids'])} observation chunks from ChromaDB")
        else:
            logger.info("No observation chunks found in ChromaDB")
    except Exception as e:
        logger.error(f"Failed to clear ChromaDB observations: {e}")

    # Step 3: Get ALL ended conversations with enough messages
    conn = sqlite3.connect(str(WORKING_DB))
    conn.row_factory = sqlite3.Row
    convs = conn.execute(
        "SELECT * FROM conversations WHERE ended_at IS NOT NULL ORDER BY started_at"
    ).fetchall()
    conn.close()

    qualifying = [c for c in convs if c["message_count"] >= OBSERVER_MIN_MESSAGES]
    skipped = len(convs) - len(qualifying)
    logger.info(f"Found {len(convs)} ended conversations. {len(qualifying)} qualify, {skipped} too short.")

    # Step 4: Observe each qualifying conversation
    client = ollama.Client(host=OLLAMA_HOST)

    for conv in qualifying:
        conv_id = conv["id"]
        messages = db.get_conversation_messages(conv_id)

        if not messages:
            logger.info(f"Skipping {conv_id[:12]}... (no messages in DB)")
            continue

        # Build transcript
        lines = []
        for msg in messages:
            readable_time = format_timestamp(msg.get("timestamp", ""))
            lines.append(f"[{readable_time}] {msg['role']}: {msg['content']}")
        transcript = "\n".join(lines)

        # Truncate if needed
        max_chars = (OBSERVER_CTX * 4) - len(OBSERVER_PROMPT) - 2000
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars]
            logger.warning(f"Transcript for {conv_id[:12]}... truncated")

        full_prompt = OBSERVER_PROMPT + transcript

        logger.info(f"Observing {conv_id[:12]}... ({len(messages)} messages, ~{len(full_prompt) // 4} tokens)")

        try:
            response = client.chat(
                model=OBSERVER_MODEL,
                messages=[{"role": "user", "content": full_prompt}],
                options={"num_ctx": OBSERVER_CTX},
            )
            characterization = response["message"]["content"].strip()
        except Exception as e:
            logger.error(f"Observer failed for {conv_id[:12]}...: {e}")
            continue

        if not characterization:
            logger.error(f"Empty characterization for {conv_id[:12]}...")
            continue

        logger.info(f"Result: {characterization[:150]}...")

        # Store in DB2
        db.save_observation(
            conversation_id=conv_id,
            content=characterization,
            message_count=len(messages),
        )

        # Store in ChromaDB
        doc_id = f"observation_{conv_id}"
        memory.ingest_document(
            doc_id=doc_id,
            text=f"Behavioral observation:\n\n{characterization}",
            title="Personality observation",
            source_type="observation",
            source_trust="secondhand",
        )

    logger.info("Done.")


if __name__ == "__main__":
    main()
