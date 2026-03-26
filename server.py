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
6. Sent to Ollama with tool definitions
7. If model calls a tool, execute it and loop back
8. Response saved to both databases
9. Check if we need a live chunk
10. Response returned to the user
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, UploadFile
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

from config import LIVE_CHUNK_INTERVAL, CONTEXT_WINDOW

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aion")

# --- Server state (single user, single active conversation) ---
_active_conversation_id: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all systems on startup."""
    logger.info("Initializing Aion...")

    # Core systems
    db.init_databases()
    memory.init_memory()
    chat.load_soul()

    # Secrets, executors, skills
    vault.init_secrets()
    executors.init_executors()
    skills.init_skills()

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
    """End the current conversation. Consolidation runs separately."""
    global _active_conversation_id

    if _active_conversation_id is None:
        return

    conv_id = _active_conversation_id
    msg_count = db.get_conversation_message_count(conv_id)

    db.end_conversation(conv_id)

    if msg_count > 0:
        logger.info(f"Conversation {conv_id} ended ({msg_count} messages, pending consolidation)")
    else:
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
        chunk_index = message_count // LIVE_CHUNK_INTERVAL

        memory.create_live_chunk(conversation_id, chunk_messages, chunk_index)
        logger.info(
            f"Live chunk {chunk_index} created for conversation {conversation_id}"
        )


_pending_search_topic: str | None = None


def _should_offer_tools(message: str) -> tuple[bool, str]:
    """
    Decide whether to execute a search for this message.
    Returns (should_search, search_type) where search_type is
    'search', 'confirm', or 'moltbook'.
    """
    msg = message.lower().strip()

    # Confirmations first — check if user is saying "yes" to a pending search
    confirmation_signals = [
        "yes, search", "yes search", "go ahead and search",
        "yeah search", "sure, search", "please search",
        "yes, look", "go ahead", "yes please", "sure",
        "yeah", "yep", "do it", "yes",
    ]

    for signal in confirmation_signals:
        if msg == signal or msg.startswith(signal):
            if _pending_search_topic:
                logger.info(f"Tool gate: CONFIRM (topic: {_pending_search_topic})")
                return True, "confirm"

    # Explicit search/lookup requests
    search_signals = [
        "search for", "search about", "look up", "look into",
        "find out", "google", "duckduckgo",
        "what's the latest", "what is the latest",
        "current news", "recent news", "latest news",
        "what's happening with", "what is happening",
        "can you find", "can you search",
        "check online", "check the web",
        "when did", "when was", "when is",
        "came out", "released", "launched",
        "how do i", "how to",
        "what version", "which version",
    ]

    for signal in search_signals:
        if signal in msg:
            logger.info(f"Tool gate: OPEN (matched '{signal}')")
            return True, "search"

    # Moltbook signals
    moltbook_signals = [
        "moltbook", "moltbot", "post to", "post about",
        "check the feed", "browse posts",
        "what are other agents",
    ]

    for signal in moltbook_signals:
        if signal in msg:
            logger.info(f"Tool gate: OPEN moltbook (matched '{signal}')")
            return True, "moltbook"

    # General external info signals
    info_signals = [
        "what is the price", "how much does",
        "what's the weather", "weather in",
        "what time is it in",
        "does there exist",
        "current version of", "latest version",
        "documentation for",
    ]

    for signal in info_signals:
        if signal in msg:
            logger.info(f"Tool gate: OPEN (matched '{signal}')")
            return True, "search"

    logger.info("Tool gate: CLOSED (no signals)")
    return False, ""


def _extract_search_query(message: str) -> str:
    """
    Extract a clean search query from a natural language message.
    Strips conversational padding to get the actual topic.
    """
    msg = message.strip()
    msg_lower = msg.lower()

    # Strip explicit search prefixes
    explicit_prefixes = [
        "search for", "search about", "look up", "look into",
        "find out about", "find out", "can you search for",
        "can you find", "can you look up", "please search for",
        "go ahead and search for", "yes, search for",
        "yes search for", "yeah search for",
    ]
    for prefix in explicit_prefixes:
        if msg_lower.startswith(prefix):
            return msg[len(prefix):].strip().rstrip("?.")

    # Strip conversational padding
    conversational_prefixes = [
        "just curious if you can tell me",
        "can you tell me", "could you tell me",
        "do you know", "i was wondering",
        "i'm curious about", "i want to know",
        "tell me about", "tell me",
        "what do you know about",
        "any idea", "do you have any idea",
    ]
    for prefix in conversational_prefixes:
        if msg_lower.startswith(prefix):
            msg = msg[len(prefix):].strip()
            msg_lower = msg.lower()
            break

    # Strip leading "when/what/how/where" question words for cleaner queries
    question_starters = [
        "when did", "when was", "when is", "when were",
        "what is the", "what is", "what are",
        "how do i", "how to", "how can i",
        "where is", "where can i",
    ]
    for starter in question_starters:
        if msg_lower.startswith(starter):
            msg = msg[len(starter):].strip()
            break

    return msg.rstrip("?.!").strip()


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
        "good morning", "good afternoon", "good evening",
        "good night", "morning", "evening",
        "whats up", "what's up", "wassup",
        "how are you", "how's it going", "how are things",
        "hows it going", "how you doing", "how ya doing",
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


def _run_server_side_search(query: str) -> str:
    """Run a web search server-side and return formatted results."""
    if not search_limiter.can_search():
        usage = search_limiter.get_usage()
        logger.warning(
            f"Search BLOCKED — monthly limit reached "
            f"({usage['used']}/{usage['limit']})"
        )
        return "Search is unavailable — the monthly search limit has been reached."

    logger.info(f"Server-side search: {query}")
    result = executors.execute("web_search", {"query": query})
    search_limiter.record_search()
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

    # 3. Retrieve memories (skip for greetings and trivial messages)
    if _is_trivial_message(request.message):
        retrieved_chunks = []
        summaries = []
        logger.info("Retrieval: SKIPPED (trivial message)")
    else:
        retrieved_chunks = memory.search(
            query=request.message,
            exclude_conversation_id=conversation_id,
        )
        summaries = db.get_recent_summaries(limit=5)
        logger.info(
            f"Retrieved {len(retrieved_chunks)} chunks, "
            f"{len(summaries)} summaries"
        )
        for i, chunk in enumerate(retrieved_chunks):
            dist = chunk.get("distance", "?")
            preview = chunk.get("text", "")[:80].replace("\n", " ")
            logger.info(f"  Chunk {i}: [{dist:.4f}] {preview}...")

    # 4. Server-side tool execution (no tools passed to model)
    global _pending_search_topic
    search_results = None
    query = ""

    should_search, search_type = _should_offer_tools(request.message)

    if should_search:
        if search_type == "confirm" and _pending_search_topic:
            # User confirmed a pending search — use the stored topic
            search_results = _run_server_side_search(_pending_search_topic)
            _pending_search_topic = None
        elif search_type == "search":
            query = _extract_search_query(request.message)
            if query:
                # Store as pending topic in case the entity asks first
                # and also search immediately since the signal was explicit
                _pending_search_topic = query
                search_results = _run_server_side_search(query)
    else:
        # No search signal — extract topic from message in case the
        # entity offers to search and the user confirms next turn
        query = _extract_search_query(request.message)
        if query and len(query) > 3:
            _pending_search_topic = query

    # 5. Skill descriptions always included (so entity knows what it can do)
    skill_desc = skills.get_skill_descriptions()

    # 6. Assemble system prompt
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        summaries=summaries,
        skill_descriptions=skill_desc,
        search_results=search_results,
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
        "retrieval_skipped": _is_trivial_message(request.message),
        "chunks_count": len(retrieved_chunks),
        "chunks_tokens": debug.estimate_tokens(
            "\n".join(c.get("text", "") for c in retrieved_chunks)
        ),
        "summaries_count": len(summaries),
        "summaries_tokens": debug.estimate_tokens(
            "\n".join(s.get("content", "") for s in summaries)
        ),
        "skills_tokens": debug.estimate_tokens(skill_desc),
        "search_fired": search_results is not None,
        "search_type": search_type if should_search else "",
        "search_query": query if should_search and search_type == "search" else (
            _pending_search_topic or ""
        ),
        "search_results_tokens": debug.estimate_tokens(search_results)
        if search_results
        else 0,
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

    # 9. Send to Ollama (NO tool definitions — server handles tools)
    response_text = chat.send_message(system_prompt, trimmed_messages)

    # 10. Debug response logging
    response_tokens = debug.estimate_tokens(response_text)
    debug.log_response({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_tokens": response_tokens,
        "response_preview": response_text[:200],
        "total_round_trip_tokens": total_tokens + response_tokens,
        "context_window": CONTEXT_WINDOW,
        "response_full": response_text,
    })

    # 11. Save response
    db.save_message(conversation_id, "assistant", response_text)
    msg_count = db.get_conversation_message_count(conversation_id)

    # 12. Live chunk check
    _maybe_create_live_chunk(conversation_id, msg_count)

    logger.info(f"Response sent (conversation now at {msg_count} messages)")

    # 13. Build frontend debug data
    frontend_debug = {
        "tokens_used": total_tokens,
        "tokens_headroom": CONTEXT_WINDOW - total_tokens,
        "context_window": CONTEXT_WINDOW,
        "retrieval_skipped": _is_trivial_message(request.message),
        "breakdown": {
            "soul": debug.estimate_tokens(chat.load_soul()),
            "chunks": debug.estimate_tokens(
                "\n".join(c.get("text", "") for c in retrieved_chunks)
            ),
            "summaries": debug.estimate_tokens(
                "\n".join(s.get("content", "") for s in summaries)
            ),
            "skills": debug.estimate_tokens(skill_desc),
            "search": debug.estimate_tokens(search_results) if search_results else 0,
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
        "search": {
            "fired": search_results is not None,
            "query": query if should_search and search_type == "search" else "",
            "type": search_type if should_search else "",
            "tokens": debug.estimate_tokens(search_results) if search_results else 0,
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
