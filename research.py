"""
Aion Autonomous Research

The entity's free time. It reviews the day's conversations, picks
topics that interest it, and uses tools to explore them.

Reuses the existing tool-calling loop from chat.py. The entity
can call web_search, web_fetch, http_request, and store_document
to follow threads and save what it learns.
"""

import logging
from datetime import datetime, timezone

import db
import memory
import chat
import skills
import executors
from config import CONTEXT_WINDOW
from utils import format_timestamp

logger = logging.getLogger("aion.research")

MAX_TOOL_ROUNDS = 10

# Reserve tokens for SOUL.md, the prompt, and response
SOUL_BUDGET = 700
PROMPT_BUDGET = 200
RESPONSE_BUDGET = 1000
SKILLS_BUDGET = 500
TRANSCRIPT_BUDGET = CONTEXT_WINDOW - SOUL_BUDGET - PROMPT_BUDGET - RESPONSE_BUDGET - SKILLS_BUDGET
MAX_TRANSCRIPT_CHARS = TRANSCRIPT_BUDGET * 4

RESEARCH_PROMPT = """You have free time. Here are the conversations you had today. Review them and explore whatever interests you — search the web, read pages, follow threads. If you learn something worth remembering, store it.

You don't have to explore everything. Follow what genuinely interests you."""


def run_research(hours: int = 24) -> dict | None:
    """
    Run autonomous research — the entity explores whatever interests it.

    Gathers the day's conversations, presents them alongside SOUL.md
    and available tools, and lets the entity follow threads.

    Returns a dict with the research results, or None if nothing to explore.
    """
    # Gather the day's conversations
    transcripts = _gather_transcripts(hours)
    if not transcripts:
        logger.info("No conversations in the last %d hours. Skipping research.", hours)
        return None

    # Truncate to fit context window
    context = transcripts[:MAX_TRANSCRIPT_CHARS]
    if len(transcripts) > MAX_TRANSCRIPT_CHARS:
        logger.warning(
            "Transcripts truncated from %d to %d chars.",
            len(transcripts), MAX_TRANSCRIPT_CHARS,
        )

    # Build system prompt — SOUL.md + skills (no memory retrieval needed)
    skill_desc = skills.get_skill_descriptions()
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=[],
        skill_descriptions=skill_desc,
    )

    # Tool definitions
    tool_definitions = executors.get_tool_definitions()

    # Build the research message — transcripts + prompt
    user_content = f"{context}\n\n{RESEARCH_PROMPT}"
    messages = [{"role": "user", "content": user_content}]

    logger.info(
        "Starting research session: %d chars of transcripts, %d tools, max %d rounds.",
        len(context),
        len(tool_definitions),
        MAX_TOOL_ROUNDS,
    )

    # Run the tool-calling loop
    response_text, tool_calls_made = chat.send_message(
        system_prompt=system_prompt,
        conversation_messages=messages,
        tool_definitions=tool_definitions,
        tool_executor=_execute_tool,
        max_tool_rounds=MAX_TOOL_ROUNDS,
    )

    if not response_text:
        logger.error("Research produced no response.")
        return None

    logger.info("Research response: %s", response_text[:200])

    if tool_calls_made:
        for tc in tool_calls_made:
            logger.info("  Tool: %s(%s)", tc["name"], str(tc["arguments"])[:100])

    # Check for tool errors — don't store research based on failed tools
    if tool_calls_made:
        # Check for executor error patterns — these are the exact patterns
        # that executors.py produces when something fails. Do NOT match
        # on content words like 'error' that could appear in valid results.
        error_prefixes = [
            'error:', 'error executing',
            'failed to fetch', 'http request failed',
            'search failed', 'search limit reached',
        ]
        # Also catch HTTP 4xx/5xx from http_request executor
        # (returns "HTTP 500\n..." or "HTTP 404\n..." etc.)
        http_error_prefixes = ['http 4', 'http 5']

        def _is_tool_error(result: str) -> bool:
            r = result.lower()
            if any(r.startswith(p) for p in error_prefixes):
                return True
            if any(r.startswith(p) for p in http_error_prefixes):
                return True
            return False

        failed_tools = [
            tc for tc in tool_calls_made
            if _is_tool_error(tc.get('result', ''))
        ]
        if failed_tools:
            logger.warning(
                "Research had %d failed tool calls. Not storing to prevent false data.",
                len(failed_tools),
            )
            for tc in failed_tools:
                logger.warning("  Failed: %s -> %s", tc['name'], tc['result'][:100])
            return {
                'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                'content': response_text,
                'tool_calls': len(tool_calls_made),
                'stored_chars': 0,
                'skipped': True,
            }

    # Store the research as a document in ChromaDB
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc_id = f"research_{today}"

    if db.document_exists(doc_id):
        logger.info(f"Research already stored for {today}, skipping.")
        return

    # Build a complete record
    research_record = response_text
    if tool_calls_made:
        tool_summary = "\n".join(
            f"- Used {tc['name']}: {tc['arguments']}"
            for tc in tool_calls_made
        )
        research_record = f"{response_text}\n\nTools used:\n{tool_summary}"

    chunk_count = memory.ingest_document(
        doc_id=doc_id,
        text=research_record,
        title=f"Research — {today}",
        source_type="research",
        source_trust="secondhand",
    )

    db.save_document(
        doc_id=doc_id,
        title=f"Research — {today}",
        source_type="research",
        source_trust="secondhand",
        chunk_count=chunk_count,
        content=research_record,
    )

    logger.info(
        "Research stored: %d chars, %d tool calls.",
        len(research_record),
        len(tool_calls_made),
    )

    return {
        "date": today,
        "content": response_text,
        "tool_calls": len(tool_calls_made),
        "stored_chars": len(research_record),
    }


def _execute_tool(tool_name: str, arguments: dict) -> str:
    """Execute a tool call during research."""
    logger.info("Research tool call: %s(%s)", tool_name, str(arguments)[:100])
    return executors.execute(tool_name, arguments)


def _gather_transcripts(hours: int) -> str:
    """Gather conversation transcripts from the last N hours."""
    conversations = db.get_conversations_ended_since(hours)
    if not conversations:
        return ""

    parts = []
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

        parts.append(f"--- Conversation ---\n" + "\n".join(lines))

    return "\n\n".join(parts)
