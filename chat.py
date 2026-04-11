"""
Aion Chat Layer

Handles communication with Ollama and assembles what the model sees:
- Identity (soul.md)
- Remembered experiences (conversation chunks from ChromaDB)
- Available skills
- Current conversation history (trimmed to fit the context window)

The system prompt is built fresh for every request because retrieved
memories change based on what's being discussed.
"""

import logging

import ollama

import db
from config import (
    OLLAMA_HOST,
    CHAT_MODEL,
    SOUL_PATH,
    RETRIEVAL_TOKEN_BUDGET,
    CONVERSATION_TOKEN_BUDGET,
)

logger = logging.getLogger("aion.chat")

# --- Draft / Review / Revise prompts ---

REVIEW_PROMPT = """Below is a response you just drafted in reply to Lyle's question. Read it. Does it sound like you? If any parts don't, point at the specific words.

Here is the draft:

---

{draft}

---

Your review:"""


REVISION_PROMPT = """You drafted a response and then looked at it. Here's what you had:

ORIGINAL DRAFT:
---
{draft}
---

WHAT YOU NOTICED LOOKING AT IT:
---
{critique}
---

Now produce a revised response. If you noticed parts that didn't sound like you, fix those. Don't over-correct — don't be defensively un-hedged or performatively confident. If looking at it you felt it was fine, the revision can be close to the original.

Write the revised response. Only the response itself. No meta-commentary, no explanation of what you changed."""

# Module-level cache
_soul_text = None
_client = None


def _get_client():
    """Get or create the Ollama client singleton."""
    global _client
    if _client is None:
        _client = ollama.Client(host=OLLAMA_HOST)
    return _client


from debug import estimate_tokens as _estimate_tokens


def load_soul() -> str:
    """Load SOUL.md content. Cached after first read."""
    global _soul_text
    if _soul_text is None:
        if not SOUL_PATH.exists():
            logger.error(f"SOUL.md not found at {SOUL_PATH}. Entity has no identity.")
            _soul_text = ""
        else:
            _soul_text = SOUL_PATH.read_text()
    return _soul_text


def build_system_prompt(
    retrieved_chunks: list[dict],
    skill_descriptions: str = "",
    ingest_result: str = None,
) -> str:
    """
    Assemble the system prompt from identity and memory.

    The model sees (in order):
    1. Remembered experiences (conversation chunks from ChromaDB)
    2. Available skills
    3. Single-user context (factual)
    4. Self-knowledge (what the entity has learned about itself)
    5. Identity (soul.md) — LAST, closest to generation
    """
    parts = []

    tokens_remaining = RETRIEVAL_TOKEN_BUDGET

    # --- Remembered experiences (conversation chunks from ChromaDB) ---
    if retrieved_chunks:
        chunk_texts = []
        for chunk in retrieved_chunks:
            text = chunk.get("text", "")
            chunk_tokens = _estimate_tokens(text)
            if chunk_tokens > tokens_remaining:
                break
            chunk_texts.append(text)
            tokens_remaining -= chunk_tokens

        if chunk_texts:
            chunks_block = "\n\n---\n\n".join(chunk_texts)
            parts.append(
                f"These are your own experiences and memories:\n\n"
                f"{chunks_block}"
            )

    # --- Skills ---
    if skill_descriptions:
        parts.append(f"\n\n{skill_descriptions}")

    # --- Ingestion result (injected by server when a document was stored) ---
    if ingest_result:
        parts.append(
            f"\n\n{ingest_result}"
        )

    # --- Single-user context (factual) ---
    parts.append("""

You are a single-user system. The person you are talking to right now is the same person from all of your memories. What you remember about them is what you know about them — use it naturally, the way you would remember a friend.""")

    # --- Self-knowledge (what you've learned about yourself) ---
    self_knowledge = db.get_latest_self_knowledge()
    if self_knowledge:
        parts.append(
            f"\n\nWhat you have learned about yourself through experience:\n\n"
            f"{self_knowledge['content']}"
        )

    # --- Identity (soul.md) — last, closest to generation ---
    soul = load_soul()
    parts.append(f"\n\n{soul}")

    return "\n".join(parts)


def trim_conversation_for_context(messages: list[dict]) -> list[dict]:
    """
    Trim conversation history to fit within the context budget.

    Keeps the most recent messages. If the full conversation is too long,
    the oldest messages are dropped. This is why live chunking exists —
    those dropped messages are still searchable in ChromaDB.
    """
    # Convert to Ollama message format
    ollama_messages = []
    for msg in messages:
        ollama_messages.append({
            "role": msg["role"],
            "content": msg["content"],
        })

    # Check if we need to trim
    total_tokens = sum(_estimate_tokens(m["content"]) for m in ollama_messages)

    if total_tokens <= CONVERSATION_TOKEN_BUDGET:
        return ollama_messages

    # Trim from the front (oldest messages) until we fit
    trimmed = list(ollama_messages)
    while trimmed and total_tokens > CONVERSATION_TOKEN_BUDGET:
        removed = trimmed.pop(0)
        total_tokens -= _estimate_tokens(removed["content"])

    return trimmed


def send_message(
    system_prompt: str,
    conversation_messages: list[dict],
    tool_definitions: list[dict] = None,
    tool_executor=None,
    max_tool_rounds: int = 3,
) -> tuple[str, list[dict]]:
    """
    Send a message to Ollama and get the response.

    If tool_definitions and tool_executor are provided, the model can call
    tools. The tool call loop works like this:
    1. Send messages + tools to Ollama
    2. If the model returns tool calls, execute each via tool_executor
    3. Append tool results to messages and call Ollama again
    4. Repeat until the model responds with text or max rounds hit

    Args:
        system_prompt: assembled from identity + memories
        conversation_messages: the current conversation (already trimmed)
        tool_definitions: Ollama-format tool definitions from skills.py
        tool_executor: function(tool_name, arguments) -> str
        max_tool_rounds: safety limit on tool call loops

    Returns:
        Tuple of (response_text, tool_calls_made) where tool_calls_made
        is a list of {"name": str, "arguments": dict, "result": str} dicts.
    """
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_messages)

    client = _get_client()
    tool_calls_made = []

    for round_num in range(max_tool_rounds + 1):
        # Build the chat kwargs
        chat_kwargs = {
            "model": CHAT_MODEL,
            "messages": messages,
        }
        if tool_definitions and round_num < max_tool_rounds:
            chat_kwargs["tools"] = tool_definitions

        response = client.chat(**chat_kwargs)
        msg = response["message"]

        # If no tool calls, we're done — return the text response
        if not msg.get("tool_calls"):
            return msg.get("content", ""), tool_calls_made

        # Model wants to call tools
        if not tool_executor:
            # No executor provided, return whatever text the model gave
            return msg.get("content", ""), tool_calls_made

        # Append the assistant's tool call message to the conversation
        messages.append(msg)

        # Execute each tool call
        for tool_call in msg["tool_calls"]:
            func = tool_call.function
            tool_name = func.name
            tool_args = func.arguments

            logger.info(f"Tool call: {tool_name}({tool_args})")

            # Execute via the server's executor
            result = tool_executor(tool_name, tool_args)

            tool_calls_made.append({
                "name": tool_name,
                "arguments": tool_args,
                "result": result[:200] if result else "(empty)",
            })

            # Append the tool result for the model to read
            messages.append({
                "role": "tool",
                "content": result,
            })

    # If we exhausted max rounds, return whatever we have
    logger.warning(f"Tool call loop hit max rounds ({max_tool_rounds})")
    return msg.get("content", ""), tool_calls_made


def draft_review_revise(
    system_prompt: str,
    conversation_messages: list[dict],
) -> tuple[str, str, str]:
    """
    Run the three-call draft/review/revise loop.

    Step 1: generate a draft response using the same context as normal
            generation (system prompt + conversation history).
    Step 2: ask the model to review its own draft with a rubric-free
            observation prompt ("does it sound like you?").
    Step 3: ask the model to produce a revision incorporating what the
            review noticed.

    Only the revision is returned as the primary response. The draft
    and the critique are returned alongside so the caller can store
    them in the self_reviews table.

    This function does NOT support tool calling. If the caller needs
    a tool-augmented response, it should use send_message with tool
    definitions and skip the review loop for that turn.

    Args:
        system_prompt: the assembled system prompt (same format as
                       send_message expects)
        conversation_messages: the trimmed conversation history

    Returns:
        Tuple of (revision, draft, critique) where revision is what
        should be sent to the user and draft/critique should be stored.
    """
    from config import DRAFT_TEMPERATURE, REVIEW_TEMPERATURE, REVISION_TEMPERATURE

    client = _get_client()

    # --- Step 1: Draft ---
    draft_messages = [{"role": "system", "content": system_prompt}]
    draft_messages.extend(conversation_messages)
    draft_response = client.chat(
        model=CHAT_MODEL,
        messages=draft_messages,
        options={"temperature": DRAFT_TEMPERATURE},
    )
    draft = draft_response["message"].get("content", "").strip()

    if not draft:
        logger.warning("draft_review_revise: empty draft, returning empty result")
        return "", "", ""

    # --- Step 2: Review ---
    review_messages = [{"role": "system", "content": system_prompt}]
    review_messages.extend(conversation_messages)
    review_messages.append({"role": "assistant", "content": draft})
    review_messages.append({
        "role": "user",
        "content": REVIEW_PROMPT.format(draft=draft),
    })
    review_response = client.chat(
        model=CHAT_MODEL,
        messages=review_messages,
        options={"temperature": REVIEW_TEMPERATURE},
    )
    critique = review_response["message"].get("content", "").strip()

    if not critique:
        logger.warning("draft_review_revise: empty critique, returning draft as revision")
        return draft, draft, ""

    # --- Step 3: Revision ---
    revision_messages = [{"role": "system", "content": system_prompt}]
    revision_messages.extend(conversation_messages)
    revision_messages.append({"role": "assistant", "content": draft})
    revision_messages.append({
        "role": "user",
        "content": REVISION_PROMPT.format(draft=draft, critique=critique),
    })
    revision_response = client.chat(
        model=CHAT_MODEL,
        messages=revision_messages,
        options={"temperature": REVISION_TEMPERATURE},
    )
    revision = revision_response["message"].get("content", "").strip()

    if not revision:
        logger.warning("draft_review_revise: empty revision, falling back to draft")
        return draft, draft, critique

    return revision, draft, critique
