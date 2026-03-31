"""
Aion Overnight Cycle

Runs batch processes while Lyle sleeps:
1. Research — entity explores topics from the day's conversations
2. Journal — entity reflects on its day
3. Observer — neutral third party characterizes entity behavior
4. Consolidation — summaries for any pending conversations (UI only)

Called by cron at 5am daily. Can also be run manually:
    python overnight.py
"""

import logging
from datetime import datetime, timezone

import db
import memory
import vault
import executors
import skills
from consolidation import consolidate_pending
from journal import run_journal
from research import run_research
from observer import run_observer
from config import LIVE_CHUNK_INTERVAL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
logger = logging.getLogger("aion.overnight")


def _end_active_conversations():
    """End all active conversations. New day, fresh start."""
    active = db.get_active_conversations()
    if not active:
        logger.info("No active conversations to close.")
        return 0

    for conv in active:
        conv_id = conv["id"]
        msg_count = conv.get("message_count", 0)

        if msg_count > 0:
            messages = db.get_conversation_messages(conv_id)
            remaining = msg_count % LIVE_CHUNK_INTERVAL
            if remaining > 0:
                chunk_messages = messages[-remaining:]
                chunk_index = memory.remainder_chunk_index(msg_count)
                memory.create_live_chunk(conv_id, chunk_messages, chunk_index)
                db.mark_conversation_chunked(conv_id)

        db.end_conversation(conv_id)
        logger.info("Ended conversation %s (%d messages).", conv_id, msg_count)

    return len(active)


def run_overnight():
    """Run all overnight processes in order."""
    start = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("OVERNIGHT CYCLE STARTING")
    logger.info("=" * 60)

    # Init systems
    db.init_databases()
    memory.init_memory()
    vault.init_secrets()
    executors.init_executors()
    skills.init_skills()

    # Step 0: Close all active conversations — new day, fresh start
    logger.info('--- Step 0: Close Active Conversations ---')
    try:
        closed = _end_active_conversations()
        if closed:
            logger.info(f'Closed {closed} active conversation(s).')
        else:
            logger.info('No active conversations.')
    except Exception as e:
        logger.error(f'Failed to close conversations: {e}')

    # Step 1: Autonomous Research
    logger.info("--- Step 1: Research ---")
    try:
        result = run_research()
        if result:
            logger.info(
                f"Research complete: {result['tool_calls']} tool calls, "
                f"{result['stored_chars']} chars stored."
            )
        else:
            logger.info("Research: nothing to explore.")
    except Exception as e:
        logger.error(f"Research failed: {e}")

    # Step 2: Journal (entity reflects on its day)
    logger.info("--- Step 2: Journal ---")
    try:
        result = run_journal()
        if result:
            logger.info(
                f"Journal entry written: {result['date']} "
                f"({result['experience_chars']} chars of experiences)"
            )
        else:
            logger.info("Journal: nothing to reflect on.")
    except Exception as e:
        logger.error(f"Journal failed: {e}")

    # Step 3: Personality observer
    logger.info("--- Step 3: Personality Observer ---")
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

    # Step 4: Consolidation (summaries for UI)
    logger.info("--- Step 4: Consolidation ---")
    try:
        consolidate_pending()
    except Exception as e:
        logger.error(f"Consolidation failed: {e}")

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info("=" * 60)
    logger.info(f"OVERNIGHT CYCLE COMPLETE ({elapsed:.1f}s)")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_overnight()
