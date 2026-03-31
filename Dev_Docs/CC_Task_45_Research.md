# CC Task 45 — Autonomous Research

## What This Is

Build `research.py` — the entity's free time to explore whatever interests it. The entity gets SOUL.md, the day's conversation transcripts, available tools, and space to follow threads it finds interesting.

Reuses existing infrastructure: `chat.send_message()` for the tool-calling loop and `executors.execute()` for tool execution.

## Files to Create

### `/home/localadmin/aion/research.py`

```python
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
from config import CHAT_MODEL, CONTEXT_WINDOW

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

    # Store the research as a document in ChromaDB
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc_id = f"research_{today}"

    # Build a complete record
    research_record = response_text
    if tool_calls_made:
        tool_summary = "\n".join(
            f"- Used {tc['name']}: {tc['arguments']}"
            for tc in tool_calls_made
        )
        research_record = f"{response_text}\n\nTools used:\n{tool_summary}"

    memory.ingest_document(
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
        chunk_count=1,
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
            readable_time = _format_timestamp(timestamp)
            lines.append(f"[{readable_time}] {role}: {content}")

        parts.append(f"--- Conversation ---\n" + "\n".join(lines))

    return "\n\n".join(parts)


def _format_timestamp(iso_timestamp: str) -> str:
    """Convert ISO timestamp to human-readable format."""
    if not iso_timestamp:
        return "unknown time"
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        return dt.strftime("%B %d, %Y at %I:%M %p")
    except (ValueError, TypeError):
        return iso_timestamp
```

## Files to Modify

### `overnight.py` — Add research as a step

Add the import at the top alongside the other imports:

```python
from research import run_research
```

Add the research step **after the journal and before the observer**. The full step ordering should be:

1. Consolidation
2. Journal
3. **Research** (new)
4. Observer

Add this block after the journal step and before the observer step:

```python
    # Step 3: Autonomous Research
    logger.info("--- Step 3: Research ---")
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
```

Renumber the observer step to "Step 4" in its log message.

## What NOT to Do

- Do NOT create a task queue table or executor. The conversations drive curiosity directly.
- Do NOT use qwen3:14b. Research is the entity's own exploration — use CHAT_MODEL (llama3.1:8b-aion).
- Do NOT add behavioral directives about what to research or how to research it.
- Do NOT search ChromaDB for the journal entry. Use conversation transcripts directly.
- Do NOT modify chat.py, server.py, config.py, memory.py, executors.py, skills.py, observer.py, or journal.py.
- Do NOT create new executors. The existing four are sufficient.

## Verification

Run research directly (requires conversations in the last 24 hours):

```bash
cd /home/localadmin/aion
python -c "
import db, memory, chat, executors, skills
db.init_databases()
memory.init_memory()
chat.load_soul()
executors.init_executors()
skills.init_skills()
from research import run_research
result = run_research()
if result:
    print(f'Tool calls: {result[\"tool_calls\"]}')
    print(f'Stored: {result[\"stored_chars\"]} chars')
    print()
    print(result['content'])
else:
    print('No research produced.')
"
```

**Check 1:** The entity uses at least one tool (web_search or web_fetch). If it doesn't use tools, it's not actually researching.

**Check 2:** The research response reads like the entity exploring something from its conversations — not a generic summary, not a list of random topics.

**Check 3:** ChromaDB has the research document:
```bash
cd /home/localadmin/aion
python -c "
import memory
memory.init_memory()
results = memory.search('research exploration')
for r in results:
    if r.get('source_type') == 'research':
        print(f'Found research: {r[\"text\"][:300]}')
"
```

**Check 4:** DB2 has the document record:
```bash
cd /home/localadmin/aion
python -c "
import db
db.init_databases()
docs = db.get_documents_since(1)
for d in docs:
    if d['source_type'] == 'research':
        print(f'{d[\"title\"]} — {d[\"source_type\"]} — {d[\"source_trust\"]}')
"
```

If all four checks pass, the task is complete.
