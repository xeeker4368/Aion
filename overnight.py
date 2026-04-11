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
import uuid
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
from pattern_recognition import run_pattern_recognition
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

            # Always mark as chunked — live chunks were created during the conversation
            db.mark_conversation_chunked(conv_id)

        db.end_conversation(conv_id)
        logger.info("Ended conversation %s (%d messages).", conv_id, msg_count)

    return len(active)


def run_overnight():
    """Run all overnight processes in order."""
    start = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    logger.info("=" * 60)
    logger.info("OVERNIGHT CYCLE STARTING")
    logger.info("=" * 60)

    # Init systems
    db.init_databases()
    memory.init_memory()
    vault.init_secrets()
    executors.init_executors()
    skills.init_skills()

    run_data = {
        "id": run_id,
        "started_at": start.isoformat(),
    }

    # Step 0: Close all active conversations
    logger.info('--- Step 0: Close Active Conversations ---')
    try:
        closed = _end_active_conversations()
        run_data["conversations_closed"] = closed
        logger.info(f'Closed {closed} active conversation(s).' if closed else 'No active conversations.')
    except Exception as e:
        logger.error(f'Failed to close conversations: {e}')
        run_data["conversations_closed"] = 0

    # Step 1: Autonomous Research
    logger.info("--- Step 1: Research ---")
    try:
        result = run_research()
        if result:
            run_data["research_status"] = "skipped" if result.get("skipped") else "success"
            run_data["research_summary"] = (
                f"{result['tool_calls']} tool calls, {result['stored_chars']} chars stored"
            )
            logger.info(f"Research complete: {run_data['research_summary']}")
        else:
            run_data["research_status"] = "skipped"
            run_data["research_summary"] = "Nothing to explore"
            logger.info("Research: nothing to explore.")
    except Exception as e:
        logger.error(f"Research failed: {e}")
        run_data["research_status"] = "failed"
        run_data["research_summary"] = str(e)[:200]

    # Step 2: Personality Observer
    logger.info("--- Step 2: Personality Observer ---")
    try:
        results = run_observer()
        if results:
            run_data["observer_status"] = "success"
            run_data["observer_summary"] = f"{len(results)} conversations characterized"
            for obs in results:
                logger.info(f"  Observed conversation {obs['conversation_id']}: {obs['message_count']} messages")
            logger.info(f"Observer: {run_data['observer_summary']}")
        else:
            run_data["observer_status"] = "skipped"
            run_data["observer_summary"] = "Nothing to observe"
            logger.info("Observer: nothing to observe.")
    except Exception as e:
        logger.error(f"Observer failed: {e}")
        run_data["observer_status"] = "failed"
        run_data["observer_summary"] = str(e)[:200]

    # Step 3: Self-Knowledge (Pattern Recognition)
    logger.info("--- Step 3: Self-Knowledge ---")
    try:
        result = run_pattern_recognition()
        if result:
            run_data["self_knowledge_status"] = "success"
            run_data["self_knowledge_summary"] = (
                f"Narrative updated ({result['observation_count']} observations, "
                f"{result['journal_count']} journals)"
            )
            logger.info(f"Self-knowledge: {run_data['self_knowledge_summary']}")
        else:
            run_data["self_knowledge_status"] = "skipped"
            run_data["self_knowledge_summary"] = "Not enough data"
            logger.info("Self-knowledge: not enough data yet.")
    except Exception as e:
        logger.error(f"Self-knowledge failed: {e}")
        run_data["self_knowledge_status"] = "failed"
        run_data["self_knowledge_summary"] = str(e)[:200]

    # Step 4: Journal
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

    # Step 5: Consolidation
    logger.info("--- Step 5: Consolidation ---")
    try:
        pending = db.get_unconsolidated_conversations()
        consolidate_pending()
        count = len(pending) if pending else 0
        run_data["consolidation_status"] = "success" if count > 0 else "skipped"
        run_data["consolidation_summary"] = (
            f"{count} conversations summarized" if count > 0 else "Nothing pending"
        )
    except Exception as e:
        logger.error(f"Consolidation failed: {e}")
        run_data["consolidation_status"] = "failed"
        run_data["consolidation_summary"] = str(e)[:200]

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    run_data["ended_at"] = datetime.now(timezone.utc).isoformat()
    run_data["duration_seconds"] = round(elapsed, 1)

    # Save run record
    try:
        db.save_overnight_run(run_data)
        logger.info("Overnight run record saved.")
    except Exception as e:
        logger.error(f"Failed to save run record: {e}")

    logger.info("=" * 60)
    logger.info(f"OVERNIGHT CYCLE COMPLETE ({elapsed:.1f}s)")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_overnight()
