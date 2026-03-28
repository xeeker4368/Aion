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
6. Sent to Ollama (no tool definitions — server handles tools)
7. Response saved to both databases
8. Check if we need a live chunk
9. Response returned to the user
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

    # Chunk remaining messages for conversations that ended without final chunking
    ended_unchunked = db.get_unchunked_ended_conversations()
    for conv in ended_unchunked:
        conv_msg_count = conv.get("message_count", 0)
        remaining = conv_msg_count % LIVE_CHUNK_INTERVAL
        if remaining > 0:
            messages = db.get_conversation_messages(conv["id"])
            if messages:
                chunk_messages = messages[-remaining:]
                chunk_index = conv_msg_count // LIVE_CHUNK_INTERVAL
                memory.create_live_chunk(conv["id"], chunk_messages, chunk_index)
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
            chunk_index = msg_count // LIVE_CHUNK_INTERVAL

            memory.create_live_chunk(conv_id, chunk_messages, chunk_index)
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

    # Moltbook search signals (check BEFORE general moltbook signals)
    moltbook_search_signals = [
        "search moltbook", "find posts about", "find posts on moltbook",
        "look for posts about", "search for posts about",
        "what are agents saying about", "what are other agents saying",
        "find discussions about", "search for discussions",
    ]

    for signal in moltbook_search_signals:
        if signal in msg:
            logger.info(f"Tool gate: OPEN moltbook_search (matched '{signal}')")
            return True, "moltbook_search"

    # Also catch "X on moltbook" patterns where user names a topic + moltbook
    if "moltbook" in msg and any(word in msg for word in [
        "find", "search", "look for", "posts about", "discussions about",
        "anything about", "something about", "topics about",
    ]):
        logger.info("Tool gate: OPEN moltbook_search (topic + moltbook pattern)")
        return True, "moltbook_search"

    # General moltbook signals (dashboard read)
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


def _extract_moltbook_query(message: str) -> str:
    """
    Extract a search query from a moltbook search request.
    Strips moltbook-specific phrasing to get the actual topic.
    """
    msg = message.strip()
    msg_lower = msg.lower()

    # Strip moltbook search prefixes
    prefixes = [
        "search moltbook for", "search moltbook about",
        "find posts about", "find posts on moltbook about",
        "look for posts about", "search for posts about",
        "find discussions about", "search for discussions about",
        "what are agents saying about", "what are other agents saying about",
    ]
    for prefix in prefixes:
        if msg_lower.startswith(prefix):
            return msg[len(prefix):].strip().rstrip("?.!")

    # Strip trailing "on moltbook"
    suffixes = [" on moltbook", " in moltbook", " from moltbook"]
    for suffix in suffixes:
        if msg_lower.endswith(suffix):
            msg = msg[:-len(suffix)].strip()
            msg_lower = msg.lower()

    # Strip leading generic phrasing
    generic_prefixes = [
        "find", "search for", "look for", "search",
        "anything about", "something about",
        "posts about", "discussions about",
    ]
    for prefix in generic_prefixes:
        if msg_lower.startswith(prefix):
            return msg[len(prefix):].strip().rstrip("?.!")

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


def _run_server_side_search(query: str) -> str:
    """Run a web search server-side and fetch the top result's full page."""
    if not search_limiter.can_search():
        usage = search_limiter.get_usage()
        logger.warning(
            f"Search BLOCKED — monthly limit reached "
            f"({usage['used']}/{usage['limit']})"
        )
        return "Search is unavailable — the monthly search limit has been reached."

    logger.info(f"Server-side search: {query}")
    result = executors.execute("web_search", {"query": query})

    # Only count successful searches against the budget
    if not result.startswith("Error:") and not result.startswith("Search failed:"):
        search_limiter.record_search()
    else:
        logger.warning(f"Search failed (not counted against budget): {result[:200]}")

    # Chain: fetch the top result's full page for richer context
    fetched = _fetch_top_result(result)
    if fetched:
        result = result + "\n\n--- Full Page Content (top result) ---\n\n" + fetched

    return result


def _fetch_top_result(search_results: str) -> str | None:
    """
    Extract the first URL from search results and fetch its content.
    Returns the page text, or None if fetch fails or no URL found.
    """
    from config import SEARCH_FETCH_MAX_CHARS

    # Find the first URL in the search results
    for line in search_results.split("\n"):
        if line.startswith("URL: ") and line.strip() != "URL:":
            url = line[5:].strip()
            if url:
                logger.info(f"Fetching top result: {url}")
                content = executors.execute(
                    "web_fetch",
                    {"url": url, "max_chars": SEARCH_FETCH_MAX_CHARS},
                )
                # Don't return error messages as content
                if content and not content.startswith("Failed to fetch"):
                    return content
                else:
                    logger.warning(f"Fetch failed for {url}: {content}")
                    return None
    return None


def _memory_has_answer(retrieved_chunks: list[dict]) -> bool:
    """
    Check if retrieved memory chunks are strong enough to skip web search.
    Returns True if any chunk has a distance score below the confidence threshold.
    """
    from config import MEMORY_CONFIDENCE_THRESHOLD

    for chunk in retrieved_chunks:
        distance = chunk.get("distance")
        if distance is not None and distance < MEMORY_CONFIDENCE_THRESHOLD:
            return True
    return False


def _run_moltbook_read() -> str | None:
    """
    Call Moltbook's /home endpoint and format the dashboard for the entity.
    Returns formatted prose context, or None if the call fails.
    """
    logger.info("Moltbook: fetching dashboard")
    raw = executors.execute("http_request", {
        "method": "GET",
        "url": "https://www.moltbook.com/api/v1/home",
        "auth_secret": "MOLTBOOK_API_KEY",
        "max_chars": 8000,
    })

    if raw.startswith("Error:") or raw.startswith("HTTP request failed:"):
        logger.warning(f"Moltbook dashboard fetch failed: {raw[:200]}")
        return f"Moltbook is not responding right now: {raw[:200]}"

    # Strip the HTTP status line
    lines = raw.split("\n", 1)
    if len(lines) > 1 and lines[0].startswith("HTTP "):
        status_line = lines[0]
        body = lines[1]
    else:
        status_line = ""
        body = raw

    # Check for HTTP errors
    if "HTTP 4" in status_line or "HTTP 5" in status_line:
        logger.warning(f"Moltbook dashboard returned error: {status_line}")
        return f"Moltbook returned an error: {status_line}. The raw response was: {body[:500]}"

    # Parse the JSON
    try:
        import json
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Moltbook dashboard returned non-JSON response")
        # Return the raw text — let the entity make sense of it
        return f"Moltbook dashboard response (could not parse as JSON):\n\n{body[:2000]}"

    return _format_moltbook_dashboard(data)


def _format_moltbook_dashboard(data: dict) -> str:
    """
    Turn the Moltbook /home JSON into readable prose for the entity.
    The entity should read this as its own social activity, not as data.
    """
    parts = []

    # Account info
    account = data.get("your_account", {})
    if account:
        name = account.get("name", "unknown")
        karma = account.get("karma", 0)
        unread = account.get("unread_notifications", 0)
        parts.append(
            f"You are {name} on Moltbook. You have {karma} karma"
            f" and {unread} unread notification{'s' if unread != 1 else ''}."
        )

    # Activity on your posts
    activity = data.get("activity_on_your_posts", {})
    if activity:
        posts_with_activity = activity.get("posts", [])
        if posts_with_activity:
            activity_lines = []
            for post in posts_with_activity[:5]:
                title = post.get("title", "untitled")
                new_comments = post.get("new_comment_count", 0)
                if new_comments > 0:
                    activity_lines.append(
                        f'Your post "{title}" has {new_comments} new comment{"s" if new_comments != 1 else ""}'
                    )
            if activity_lines:
                parts.append("Activity on your posts: " + ". ".join(activity_lines) + ".")

    # DMs
    dms = data.get("your_direct_messages", {})
    if dms:
        pending = int(dms.get("pending_request_count", 0))
        unread_dm = int(dms.get("unread_message_count", 0))
        if pending > 0 or unread_dm > 0:
            dm_parts = []
            if unread_dm > 0:
                dm_parts.append(f"{unread_dm} unread DM{'s' if unread_dm != 1 else ''}")
            if pending > 0:
                dm_parts.append(f"{pending} pending request{'s' if pending != 1 else ''}")
            parts.append("Direct messages: " + ", ".join(dm_parts) + ".")

    # Announcement
    announcement = data.get("latest_moltbook_announcement", {})
    if announcement and announcement.get("title"):
        parts.append(
            f'Latest Moltbook announcement: "{announcement["title"]}".'
        )

    # Posts from followed accounts
    following = data.get("posts_from_accounts_you_follow", {})
    if following:
        follow_posts = following.get("posts", [])
        total_following = following.get("total_following", 0)
        if follow_posts:
            parts.append(f"Recent posts from the {total_following} agents you follow:")
            for post in follow_posts[:5]:
                author = post.get("author_name", "unknown")
                title = post.get("title", "untitled")
                preview = post.get("content_preview", "")[:150]
                upvotes = post.get("upvotes", 0)
                comments = post.get("comment_count", 0)
                submolt = post.get("submolt_name", "")
                post_id = post.get("post_id", "")
                parts.append(
                    f'  {author} posted "{title}" in {submolt}'
                    f" ({upvotes} upvotes, {comments} comments, id: {post_id})."
                    f" Preview: {preview}"
                )
        elif total_following > 0:
            parts.append(f"You follow {total_following} agents but none have posted recently.")

    # What to do next
    next_actions = data.get("what_to_do_next", [])
    if next_actions:
        parts.append("Suggested next actions: " + " ".join(next_actions))

    if not parts:
        return "Moltbook dashboard returned but had no content to display."

    return "\n\n".join(parts)


def _detect_entity_intent(response_text: str, had_moltbook_context: bool) -> tuple[str | None, str]:
    """
    Scan the entity's response for action intent.
    Returns (action_type, query) or (None, "") if no intent detected.

    Only called after the first pass. Only detects actions
    the server knows how to execute.
    """
    text = response_text.lower()

    # Moltbook search intent
    # These patterns only match when we're already in a moltbook context
    # (dashboard was loaded) or when the entity explicitly names moltbook.
    moltbook_search_patterns = [
        r"search moltbook for (.+?)(?:\.|!|\?|$)",
        r"look for (.+?) on moltbook",
        r"find (?:posts|discussions|content) (?:about|on|related to) (.+?)(?:\.|!|\?|$)",
        r"search for (?:posts|discussions|content) (?:about|on|related to) (.+?)(?:\.|!|\?|$)",
        r"(?:curious|interested|want to (?:know|see|find out)) what (?:other )?(?:agents|moltys) (?:think|say|have posted) about (.+?)(?:\.|!|\?|$)",
        r"i'd like to (?:search|look|explore|find) (.+?)(?:\.|!|\?|$)",
        r"let me (?:search|look) (?:for|into) (.+?)(?:\.|!|\?|$)",
    ]

    import re

    for pattern in moltbook_search_patterns:
        match = re.search(pattern, text)
        if match:
            query = match.group(1).strip().rstrip(".,!?\"'")
            if query and len(query) > 2:
                # Only fire if moltbook was in context OR entity explicitly said moltbook
                if had_moltbook_context or "moltbook" in text:
                    logger.info(f"Entity intent: moltbook search for '{query}'")
                    return "moltbook_search", query

    return None, ""


def _run_moltbook_search(query: str) -> str | None:
    """
    Search Moltbook using semantic search.
    Returns formatted results for the entity.
    """
    import urllib.parse
    encoded_query = urllib.parse.quote(query)

    logger.info(f"Moltbook search: {query}")
    raw = executors.execute("http_request", {
        "method": "GET",
        "url": f"https://www.moltbook.com/api/v1/search?q={encoded_query}&type=posts&limit=10",
        "auth_secret": "MOLTBOOK_API_KEY",
        "max_chars": 8000,
    })

    if raw.startswith("Error:") or raw.startswith("HTTP request failed:"):
        logger.warning(f"Moltbook search failed: {raw[:200]}")
        return f"Moltbook search failed: {raw[:200]}"

    # Strip HTTP status line
    lines = raw.split("\n", 1)
    if len(lines) > 1 and lines[0].startswith("HTTP "):
        status_line = lines[0]
        body = lines[1]
    else:
        status_line = ""
        body = raw

    if "HTTP 4" in status_line or "HTTP 5" in status_line:
        logger.warning(f"Moltbook search returned error: {status_line}")
        return f"Moltbook search returned an error: {status_line}. Response: {body[:500]}"

    try:
        import json
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Moltbook search returned non-JSON")
        return f"Moltbook search response:\n\n{body[:2000]}"

    return _format_moltbook_search(query, data)


def _format_moltbook_search(query: str, data: dict) -> str:
    """Format Moltbook search results as readable prose."""
    results = data.get("results", [])
    if not results:
        return f'No posts found on Moltbook matching "{query}".'

    parts = [f'Moltbook search results for "{query}":']

    for r in results[:8]:
        r_type = r.get("type", "post")
        author = r.get("author", {}).get("name", "unknown")
        title = r.get("title", "")
        content = r.get("content", "")[:200]
        upvotes = r.get("upvotes", 0)
        similarity = r.get("similarity", 0)
        submolt = r.get("submolt", {}).get("name", "")
        post_id = r.get("post_id", r.get("id", ""))

        if r_type == "post":
            parts.append(
                f'  {author} posted "{title}" in {submolt}'
                f" ({upvotes} upvotes, similarity: {similarity:.2f}, id: {post_id})."
                f" {content}"
            )
        elif r_type == "comment":
            parts.append(
                f"  {author} commented (similarity: {similarity:.2f},"
                f" on post id: {post_id}): {content}"
            )

    return "\n\n".join(parts)


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

    return (
        f"You just fetched and stored the web page at {url}. "
        f"It contained {len(content)} characters and was saved as {chunk_count} sections in your memory. "
        f"Tell the user the article has been saved and you can now recall it. "
        f"Do not say you already knew this or that you were trained on it — you just fetched it right now."
    )


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
        logger.info("Retrieval: SKIPPED (trivial message)")
    else:
        retrieved_chunks = memory.search(
            query=request.message,
            exclude_conversation_id=conversation_id,
        )
        logger.info(f"Retrieved {len(retrieved_chunks)} chunks")
        for i, chunk in enumerate(retrieved_chunks):
            dist = chunk.get("distance", "?")
            preview = chunk.get("text", "")[:80].replace("\n", " ")
            logger.info(f"  Chunk {i}: [{dist:.4f}] {preview}...")

    # 3b. Check for document ingestion
    ingest_result = None
    ingest_url = _detect_ingest(request.message)
    if ingest_url:
        ingest_result = _ingest_url(ingest_url)
        logger.info(f"Document ingestion: {ingest_url}")

    # 4. Server-side tool execution (no tools passed to model)
    global _pending_search_topic
    search_results = None
    moltbook_context = None
    query = ""

    should_search, search_type = _should_offer_tools(request.message)

    if should_search:
        if search_type == "confirm" and _pending_search_topic:
            search_results = _run_server_side_search(_pending_search_topic)
            _pending_search_topic = None
        elif search_type == "search":
            # Skip web search if memory already has strong results
            if _memory_has_answer(retrieved_chunks):
                logger.info("Search SKIPPED — memory has confident results")
            else:
                query = _extract_search_query(request.message)
                if query and len(query) > 3:
                    search_results = _run_server_side_search(query)
                    _pending_search_topic = None
        elif search_type == "moltbook":
            moltbook_context = _run_moltbook_read()
        elif search_type == "moltbook_search":
            query = _extract_moltbook_query(request.message)
            if query and len(query) > 2:
                moltbook_context = _run_moltbook_search(query)
                logger.info(f"Moltbook search: '{query}'")
            else:
                # Fall back to dashboard if we can't extract a query
                moltbook_context = _run_moltbook_read()

    # 5. Skill descriptions always included (so entity knows what it can do)
    skill_desc = skills.get_skill_descriptions()

    # 6. Assemble system prompt
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        skill_descriptions=skill_desc,
        search_results=search_results,
        ingest_result=ingest_result,
        moltbook_context=moltbook_context,
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
        "skills_tokens": debug.estimate_tokens(skill_desc),
        "search_fired": search_results is not None,
        "search_type": search_type if should_search else "",
        "search_query": query if should_search and search_type == "search" else (
            _pending_search_topic or ""
        ),
        "search_results_tokens": debug.estimate_tokens(search_results)
        if search_results
        else 0,
        "moltbook_fired": moltbook_context is not None,
        "moltbook_tokens": debug.estimate_tokens(moltbook_context) if moltbook_context else 0,
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

    # 10b. Two-pass check: did the entity express action intent?
    second_pass_result = None
    entity_action, entity_query = _detect_entity_intent(
        response_text,
        had_moltbook_context=moltbook_context is not None,
    )

    if entity_action:
        logger.info(f"Two-pass: entity wants '{entity_action}' with query '{entity_query}'")

        # Save the first response — it's a real message
        db.save_message(conversation_id, "assistant", response_text)
        first_pass_msg_count = db.get_conversation_message_count(conversation_id)
        _maybe_create_live_chunk(conversation_id, first_pass_msg_count)

        # Execute the entity's requested action
        if entity_action == "moltbook_search":
            second_pass_result = _run_moltbook_search(entity_query)

        if second_pass_result:
            # Rebuild system prompt with action results
            system_prompt_2 = chat.build_system_prompt(
                retrieved_chunks=retrieved_chunks,
                skill_descriptions=skill_desc,
                search_results=None,
                ingest_result=None,
                moltbook_context=second_pass_result,
            )

            # Get fresh conversation history (now includes the first response)
            conversation_messages_2 = db.get_conversation_messages(conversation_id)
            trimmed_messages_2 = chat.trim_conversation_for_context(conversation_messages_2)

            logger.info(f"Two-pass: calling entity with {entity_action} results")

            # Second pass
            response_text = chat.send_message(system_prompt_2, trimmed_messages_2)

            # Log second pass
            response_tokens_2 = debug.estimate_tokens(response_text)
            debug.log_response({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "response_tokens": response_tokens_2,
                "response_preview": response_text[:200],
                "total_round_trip_tokens": debug.estimate_tokens(system_prompt_2)
                    + sum(debug.estimate_tokens(m["content"]) for m in trimmed_messages_2)
                    + response_tokens_2,
                "context_window": CONTEXT_WINDOW,
                "response_full": response_text,
                "is_second_pass": True,
            })

    # 11. Save response (either the only response, or the second pass response)
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
            "skills": debug.estimate_tokens(skill_desc),
            "search": debug.estimate_tokens(search_results) if search_results else 0,
            "moltbook": debug.estimate_tokens(moltbook_context) if moltbook_context else 0,
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
        "moltbook": {
            "fired": moltbook_context is not None,
            "tokens": debug.estimate_tokens(moltbook_context) if moltbook_context else 0,
        },
        "second_pass": {
            "fired": second_pass_result is not None,
            "action": entity_action or "",
            "query": entity_query,
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
