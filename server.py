"""
Aion Server

The entry point. Connects all layers:
- Database (archive + working store)
- Memory (ChromaDB vector search)
- Chat (Ollama + prompt assembly)
- Secrets (encrypted credential storage)
- Executors (built-in tools: http, web fetch, search, etc.)
- Skills (drop-in SKILL.md capabilities)

Message lifecycle:
1. User sends a message
2. Message saved to both databases (dual-write)
3. Vector search finds relevant memories from past conversations
4. System prompt assembled: SOUL.md + memories + skill descriptions
5. Current conversation history trimmed to fit context window
6. Sent to Ollama with tool definitions from skills
7. If model calls tools, server executes and returns results
8. Response saved to both databases
9. Check if we need a live chunk
10. Response returned to the user
"""

import logging
import re as _re
import uuid as _uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import memory
import chat
import vault
import executors
import skills
import debug
import search_limiter

from config import LIVE_CHUNK_INTERVAL, CONTEXT_WINDOW, DEV_MODE
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aion")

# --- Server state (single user, single active conversation) ---
_active_conversation_id: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all systems on startup."""
    logger.info("Initializing Aion...")

    # Dev mode: copy production databases if dev directory is fresh
    if DEV_MODE:
        from pathlib import Path
        import shutil
        import sqlite3

        dev_dir = Path(config.DATA_DIR) / "dev"
        prod_archive = Path(config.DATA_DIR) / "archive.db"
        prod_working = Path(config.DATA_DIR) / "working.db"
        prod_chroma = Path(config.DATA_DIR) / "chromadb"

        dev_dir.mkdir(parents=True, exist_ok=True)

        # Copy SQLite databases using backup API (safe for live DBs)
        if prod_archive.exists() and not (dev_dir / "archive.db").exists():
            src = sqlite3.connect(str(prod_archive))
            dst = sqlite3.connect(str(dev_dir / "archive.db"))
            src.backup(dst)
            dst.close()
            src.close()
            logger.info("DEV MODE: Copied archive.db to dev/")

        if prod_working.exists() and not (dev_dir / "working.db").exists():
            src = sqlite3.connect(str(prod_working))
            dst = sqlite3.connect(str(dev_dir / "working.db"))
            src.backup(dst)
            dst.close()
            src.close()
            logger.info("DEV MODE: Copied working.db to dev/")

        # Copy ChromaDB directory
        if prod_chroma.exists() and not (dev_dir / "chromadb").exists():
            shutil.copytree(str(prod_chroma), str(dev_dir / "chromadb"))
            logger.info("DEV MODE: Copied chromadb/ to dev/")

        logger.warning("=" * 50)
        logger.warning("  DEV MODE ACTIVE — using data/dev/")
        logger.warning("  Production data is NOT being modified.")
        logger.warning("  Delete data/dev/ to reset.")
        logger.warning("=" * 50)

    # Core systems
    db.init_databases()
    memory.init_memory()
    chat.load_soul()

    # Secrets, executors, skills
    vault.init_secrets()
    executors.init_executors()
    skills.init_skills()

    # Chunk remaining messages for conversations that ended without final chunking
    ended_unchunked = db.get_unchunked_ended_conversations()
    for conv in ended_unchunked:
        conv_msg_count = conv.get("message_count", 0)
        remaining = conv_msg_count % LIVE_CHUNK_INTERVAL
        if remaining > 0:
            messages = db.get_conversation_messages(conv["id"])
            if messages:
                chunk_messages = messages[-remaining:]
                chunk_index = memory.remainder_chunk_index(conv_msg_count)
                memory.create_live_chunk(conv["id"], chunk_messages, chunk_index)
                db.mark_conversation_chunked(conv["id"])
                logger.info(
                    f"Startup: chunked {remaining} remaining messages for {conv['id']}"
                )

    # Check for conversations that need consolidation
    global _active_conversation_id
    pending = db.get_unconsolidated_conversations()
    if pending:
        logger.info(
            f"{len(pending)} conversations pending consolidation. "
            f"Run 'python consolidation.py' to process them."
        )

    debug.init_debug()
    debug.log_startup_banner()

    if DEV_MODE:
        logger.warning("Aion ready. (DEV MODE)")
    else:
        logger.info("Aion ready.")
    yield
    logger.info("Aion shutting down.")

    if _active_conversation_id:
        _end_active_conversation()


app = FastAPI(title="Aion", lifespan=lifespan)

# Serve static files (the chat UI)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============================================================
# Request/Response models
# ============================================================

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    memories_used: int
    tools_used: list[str] = []
    debug: dict = {}


class SecretRequest(BaseModel):
    key: str
    value: str


class SkillInstallRequest(BaseModel):
    content: str
    filename: str = "SKILL.md"


# ============================================================
# Conversation helpers
# ============================================================

def _end_active_conversation():
    """End the current conversation and chunk any remaining messages."""
    global _active_conversation_id

    if _active_conversation_id is None:
        return

    conv_id = _active_conversation_id
    msg_count = db.get_conversation_message_count(conv_id)

    if msg_count > 0:
        # Chunk any messages beyond the last live chunk boundary
        remaining = msg_count % LIVE_CHUNK_INTERVAL
        if remaining > 0:
            messages = db.get_conversation_messages(conv_id)
            chunk_messages = messages[-remaining:]
            chunk_index = memory.remainder_chunk_index(msg_count)

            memory.create_live_chunk(conv_id, chunk_messages, chunk_index)
            db.mark_conversation_chunked(conv_id)
            logger.info(
                f"Final chunk {chunk_index} created for conversation {conv_id} "
                f"({remaining} remaining messages)"
            )

        db.end_conversation(conv_id)
        logger.info(f"Conversation {conv_id} ended ({msg_count} messages)")
    else:
        db.end_conversation(conv_id)
        logger.info(f"Conversation {conv_id} ended (empty)")

    _active_conversation_id = None


def _ensure_active_conversation() -> str:
    """Make sure there's an active conversation. Start one if needed."""
    global _active_conversation_id

    if _active_conversation_id is None:
        _active_conversation_id = db.start_conversation()
        logger.info(f"Started conversation {_active_conversation_id}")

    return _active_conversation_id


def _maybe_create_live_chunk(conversation_id: str, message_count: int):
    """Create a live chunk if we've hit the interval."""
    if memory.should_create_live_chunk(message_count):
        messages = db.get_conversation_messages(conversation_id)
        chunk_messages = messages[-LIVE_CHUNK_INTERVAL:]
        chunk_index = memory.live_chunk_index(message_count)

        memory.create_live_chunk(conversation_id, chunk_messages, chunk_index)
        logger.info(
            f"Live chunk {chunk_index} created for conversation {conversation_id}"
        )


def _is_trivial_message(message: str) -> bool:
    """
    Detect greetings and trivial messages that don't need memory retrieval.

    The rule is simple: short messages matching common greeting/filler
    patterns skip retrieval. Everything else triggers a search.
    """
    msg = message.lower().strip().rstrip("!?.")

    # Very short messages are almost always greetings or filler
    words = msg.split()
    if len(words) > 8:
        return False

    trivial_patterns = [
        # Greetings
        "hi", "hey", "hello", "howdy", "yo", "sup",
        "hi there", "hey there", "hello there",
        "hi again", "hey again", "hello again",
        "hi there again", "hey there again",
        "im back", "i'm back",
        "good morning", "good afternoon", "good evening",
        "good night", "morning", "evening",
        "whats up", "what's up", "wassup",
        "how are you", "how's it going", "how are things",
        "hows it going", "how you doing", "how ya doing",
        # Extended greetings
        "how are you today", "hows it going today",
        "how are you doing", "how are you doing today",
        "how you doing today", "whats going on",
        "what's going on", "not much", "nm",
        # Confirmations
        "ok", "okay", "sure", "thanks", "thank you",
        "got it", "cool", "nice", "great", "awesome",
        "sounds good", "makes sense", "understood",
        "yep", "yup", "yeah", "yes", "no", "nope", "nah",
        # Farewells
        "bye", "goodbye", "see you", "see ya", "later",
        "goodnight", "good night", "take care",
        "talk to you later", "ttyl",
    ]

    return msg in trivial_patterns


def _targets_realtime_skill(message: str) -> bool:
    """
    Detect if a message is asking about a realtime skill's domain.

    Realtime data should come from live tool calls, not from memory.
    When someone asks "what's on moltbook?" or "check the submolts"
    they want current data, not memories of what was there last week.

    Trigger keywords are defined in each skill's SKILL.md frontmatter.

    Returns True if retrieval should be skipped in favor of tool calls.
    """
    msg = message.lower().strip()

    realtime_skills = [s for s in skills.get_ready_skills() if s.get("realtime")]

    for skill in realtime_skills:
        triggers = skill.get("triggers", [skill["name"]])
        for trigger in triggers:
            if trigger.lower() in msg:
                return True

    return False


def _has_tool_intent(response_text: str) -> bool:
    """
    Detect if the entity's response expresses intent to use a tool.

    The entity sees skill descriptions and knows what tools exist.
    When it wants to use one, it mentions it in its response.
    This checks for those signals.

    Must be broad enough to catch natural language variations.
    A miss here means the entity fabricates results instead of
    calling the real API.
    """
    if not response_text:
        return False

    text = response_text.lower()

    # Tool name signals (exact names and natural language variants)
    tool_signals = [
        "web_search", "web_fetch", "http_request", "http request",
        "let me search", "let me look", "i'll search", "i'll look up",
        "i can search", "i can look up", "let me check",
        "i'll check moltbook", "let me check moltbook",
        "search for", "look up", "look that up",
        "search the web", "check the web",
        "use the api", "call the api", "check the api",
        "making a request", "making an http",
    ]

    if any(signal in text for signal in tool_signals):
        return True

    # If the response contains any known API endpoint URL, that's intent
    if "moltbook.com/api/" in text:
        return True

    return False


def _detect_ingest(message: str) -> str | None:
    """
    Detect if the user wants to ingest a URL.
    Returns the URL if found, None otherwise.
    """
    msg = message.lower().strip()

    ingest_signals = [
        "remember this", "save this", "store this",
        "read this", "ingest this", "absorb this",
        "add this to your memory", "add this to memory",
        "remember this article", "remember this page",
        "save this article", "save this page",
        "read and remember", "learn this",
    ]

    has_signal = any(signal in msg for signal in ingest_signals)
    if not has_signal:
        return None

    # Extract URL from the message
    url_pattern = r'https?://[^\s<>"\']+'
    match = _re.search(url_pattern, message)
    if match:
        return match.group(0).rstrip(".,;:!?")
    return None


def _ingest_url(url: str) -> str:
    """
    Fetch a URL, chunk it, store in ChromaDB, record in DB2.
    Returns a confirmation string to inject into the system prompt.
    """
    # Fetch the page (use a larger limit than default for ingestion)
    content = executors.execute("web_fetch", {"url": url, "max_chars": 20000})

    if content.startswith("Failed to fetch"):
        return f"Could not fetch {url}: {content}"

    if len(content.strip()) < 100:
        return f"The page at {url} had very little readable content."

    # Extract a title from the first line or use the URL
    lines = content.strip().split("\n")
    title = lines[0][:120] if lines else url

    # Create a document ID and store
    doc_id = str(_uuid.uuid4())

    # Chunk and embed into ChromaDB
    chunk_count = memory.ingest_document(
        doc_id=doc_id,
        text=content,
        title=title,
        source_type="article",
        source_trust="thirdhand",
    )

    # Record in DB2 for UI and batch summarization
    db.save_document(
        doc_id=doc_id,
        title=title,
        url=url,
        source_type="article",
        source_trust="thirdhand",
        chunk_count=chunk_count,
    )

    logger.info(
        f"Ingested {url}: {len(content)} chars, {chunk_count} chunks, doc_id={doc_id}"
    )

    return f"The web page at {url} was fetched and saved to memory. {len(content)} characters, {chunk_count} sections."


# ============================================================
# Tool execution
# ============================================================

def _execute_tool_call(tool_name: str, arguments: dict) -> str:
    """
    Execute a tool call from the model.
    The model calls generic executors directly (http_request, web_search, etc.)
    with all arguments constructed from SKILL.md documentation.
    """
    logger.info(f"Executing tool: {tool_name} with {list(arguments.keys())}")
    result = executors.execute(tool_name, arguments)
    return result


# ============================================================
# Chat endpoints
# ============================================================

@app.get("/")
async def serve_ui():
    """Serve the chat interface."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    """The main message handler."""
    conversation_id = _ensure_active_conversation()

    # 1. Save user message
    db.save_message(conversation_id, "user", request.message)
    msg_count = db.get_conversation_message_count(conversation_id)
    logger.info(f"User message saved (message #{msg_count})")

    # 2. Live chunk check
    _maybe_create_live_chunk(conversation_id, msg_count)

    # 3. Retrieve memories (skip for greetings and realtime skill queries)
    if _is_trivial_message(request.message):
        retrieved_chunks = []
        logger.info("Retrieval: SKIPPED (trivial message)")
    elif _targets_realtime_skill(request.message):
        retrieved_chunks = []
        logger.info("Retrieval: SKIPPED (realtime skill — use live data)")
    else:
        retrieved_chunks = memory.search(
            query=request.message,
            exclude_conversation_id=conversation_id,
        )
        logger.info(f"Retrieved {len(retrieved_chunks)} chunks")
        for i, chunk in enumerate(retrieved_chunks):
            dist = chunk.get("distance")
            dist_str = f"{dist:.4f}" if isinstance(dist, (int, float)) else "?"
            preview = chunk.get("text", "")[:80].replace("\n", " ")
            logger.info(f"  Chunk {i}: [{dist_str}] {preview}...")

    # 4. Check for document ingestion (special case — not a tool call)
    ingest_result = None
    ingest_url = _detect_ingest(request.message)
    if ingest_url:
        ingest_result = _ingest_url(ingest_url)
        logger.info(f"Document ingestion: {ingest_url}")

    # 5. Skill descriptions for system prompt
    skill_desc = skills.get_skill_descriptions(request.message)

    # 6. Assemble system prompt
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        skill_descriptions=skill_desc,
        ingest_result=ingest_result,
    )

    # 7. Trim conversation for context
    conversation_messages = db.get_conversation_messages(conversation_id)
    trimmed_messages = chat.trim_conversation_for_context(conversation_messages)

    # 8. Debug logging
    total_tokens = debug.estimate_tokens(system_prompt) + sum(
        debug.estimate_tokens(m["content"]) for m in trimmed_messages
    )
    request_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conversation_id": conversation_id,
        "message_number": msg_count,
        "user_message": request.message,
        "retrieval_skipped": _is_trivial_message(request.message) or _targets_realtime_skill(request.message),
        "chunks_count": len(retrieved_chunks),
        "chunks_tokens": debug.estimate_tokens(
            "\n".join(c.get("text", "") for c in retrieved_chunks)
        ),
        "skills_tokens": debug.estimate_tokens(skill_desc),
        "tools_count": 0,
        "soul_tokens": debug.estimate_tokens(chat.load_soul()),
        "system_prompt_total_tokens": debug.estimate_tokens(system_prompt),
        "conversation_history_tokens": sum(
            debug.estimate_tokens(m["content"]) for m in trimmed_messages
        ),
        "conversation_messages_sent": len(trimmed_messages),
        "conversation_messages_total": len(conversation_messages),
        "messages_trimmed": len(conversation_messages) - len(trimmed_messages),
        "total_tokens": total_tokens,
        "context_window": CONTEXT_WINDOW,
        "budget_exceeded": total_tokens > CONTEXT_WINDOW,
        "headroom": CONTEXT_WINDOW - total_tokens,
        "system_prompt_full": system_prompt,
    }
    debug.log_request(request_data)

    # 9. Two-pass tool calling
    # Pass 1: No tool definitions — entity responds naturally
    # If it needs tools, it says so. If it doesn't, we're done.
    response_text, tool_calls_made = chat.send_message(
        system_prompt,
        trimmed_messages,
    )

    # Pass 2: If entity expressed tool intent, re-call with tools enabled
    if not _is_trivial_message(request.message) and _has_tool_intent(response_text):
        logger.info("Tool intent detected in response. Re-calling with tools.")
        tool_definitions = executors.get_tool_definitions()
        response_text, tool_calls_made = chat.send_message(
            system_prompt,
            trimmed_messages,
            tool_definitions=tool_definitions,
            tool_executor=_execute_tool_call,
        )

    # 11. Log response and tool usage
    response_tokens = debug.estimate_tokens(response_text)
    debug.log_response({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_tokens": response_tokens,
        "response_preview": response_text[:200],
        "total_round_trip_tokens": total_tokens + response_tokens,
        "context_window": CONTEXT_WINDOW,
        "response_full": response_text,
        "tool_calls": tool_calls_made,
    })

    if tool_calls_made:
        for tc in tool_calls_made:
            logger.info(f"Tool used: {tc['name']}({tc['arguments']}) -> {tc['result'][:100]}")

    # 12. Save response
    db.save_message(conversation_id, "assistant", response_text)
    msg_count = db.get_conversation_message_count(conversation_id)

    # 13. Live chunk check
    _maybe_create_live_chunk(conversation_id, msg_count)

    logger.info(f"Response sent (conversation now at {msg_count} messages)")

    # 14. Build frontend debug data
    frontend_debug = {
        "tokens_used": total_tokens,
        "tokens_headroom": CONTEXT_WINDOW - total_tokens,
        "context_window": CONTEXT_WINDOW,
        "retrieval_skipped": _is_trivial_message(request.message) or _targets_realtime_skill(request.message),
        "breakdown": {
            "soul": debug.estimate_tokens(chat.load_soul()),
            "chunks": debug.estimate_tokens(
                "\n".join(c.get("text", "") for c in retrieved_chunks)
            ),
            "skills": debug.estimate_tokens(skill_desc),
            "history": sum(
                debug.estimate_tokens(m["content"]) for m in trimmed_messages
            ),
        },
        "chunks": [
            {
                "preview": c.get("text", "")[:120],
                "distance": round(c.get("distance", 0), 4),
                "conversation_id": c.get("conversation_id", ""),
            }
            for c in retrieved_chunks
        ],
        "tools": {
            "calls_count": len(tool_calls_made),
            "calls_made": [
                {"name": tc["name"], "arguments": tc["arguments"]}
                for tc in tool_calls_made
            ],
        },
        "conversation": {
            "messages_sent": len(trimmed_messages),
            "messages_total": len(conversation_messages),
            "messages_trimmed": len(conversation_messages) - len(trimmed_messages),
        },
    }

    return ChatResponse(
        response=response_text,
        conversation_id=conversation_id,
        memories_used=len(retrieved_chunks),
        tools_used=[tc["name"] for tc in tool_calls_made],
        debug=frontend_debug,
    )


@app.post("/api/conversation/new")
async def new_conversation():
    """End the current conversation and start a fresh one."""
    _end_active_conversation()
    conversation_id = _ensure_active_conversation()
    return {"conversation_id": conversation_id}


@app.get("/api/conversation/current")
async def get_current_conversation():
    """Get the current active conversation and its messages."""
    conversation_id = _ensure_active_conversation()
    messages = db.get_conversation_messages(conversation_id)

    return {
        "conversation_id": conversation_id,
        "messages": [
            {"role": m["role"], "content": m["content"], "timestamp": m["timestamp"]}
            for m in messages
        ],
    }


@app.get("/api/conversations")
async def list_conversations():
    """List recent conversations."""
    conversations = db.get_recent_conversations()
    return {"conversations": conversations}


# ============================================================
# Settings endpoints (secrets management)
# ============================================================

@app.get("/settings")
async def serve_settings():
    """Serve the settings page."""
    return FileResponse(str(STATIC_DIR / "settings.html"))


@app.get("/api/secrets")
async def list_secrets():
    """List all secret key names (values are never exposed)."""
    keys = vault.list_keys()
    return {"secrets": keys}


@app.post("/api/secrets")
async def add_secret(request: SecretRequest):
    """Add or update a secret."""
    vault.set_secret(request.key, request.value)
    # Refresh skill status in case this satisfies a requirement
    skills.refresh_skill_status()
    return {"status": "saved", "key": request.key}


@app.delete("/api/secrets/{key}")
async def delete_secret(key: str):
    """Delete a secret."""
    deleted = vault.delete(key)
    if deleted:
        skills.refresh_skill_status()
        return {"status": "deleted", "key": key}
    return JSONResponse(status_code=404, content={"error": "Secret not found"})


# ============================================================
# Skills endpoints
# ============================================================

@app.get("/api/skills")
async def list_all_skills():
    """List all installed skills with their status."""
    return {"skills": skills.list_skills()}


@app.post("/api/skills/install")
async def install_skill(request: SkillInstallRequest):
    """Install a skill from SKILL.md content."""
    result = skills.install_skill(request.filename, request.content)
    if "error" in result:
        return JSONResponse(status_code=400, content=result)
    return result


@app.delete("/api/skills/{name}")
async def uninstall_skill(name: str):
    """Uninstall a skill."""
    removed = skills.uninstall_skill(name)
    if removed:
        return {"status": "removed", "name": name}
    return JSONResponse(status_code=404, content={"error": "Skill not found"})


@app.get("/api/search/usage")
async def search_usage():
    """Get current search API usage stats."""
    return search_limiter.get_usage()


@app.get("/api/executors")
async def list_all_executors():
    """List available executors."""
    return {"executors": executors.list_executors()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
