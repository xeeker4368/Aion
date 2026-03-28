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

from config import (
    OLLAMA_HOST,
    CHAT_MODEL,
    SOUL_PATH,
    RETRIEVAL_TOKEN_BUDGET,
    CONVERSATION_TOKEN_BUDGET,
)

logger = logging.getLogger("aion.chat")

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
        _soul_text = SOUL_PATH.read_text()
    return _soul_text


def build_system_prompt(
    retrieved_chunks: list[dict],
    skill_descriptions: str = "",
    search_results: str = None,
    ingest_result: str = None,
    moltbook_context: str = None,
) -> str:
    """
    Assemble the system prompt from identity and memory.

    The model sees (in order):
    1. Identity (soul.md)
    2. Remembered experiences (conversation chunks from ChromaDB)
    3. Available skills
    4. Search results (if any)
    5. Behavioral guidance
    """
    soul = load_soul()
    parts = [soul]

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
                f"\n\nThese are your own experiences and memories:\n\n"
                f"{chunks_block}"
            )

    # --- Skills ---
    if skill_descriptions:
        parts.append(f"\n\n{skill_descriptions}")

    # --- Search results (injected by server when relevant) ---
    if search_results:
        parts.append(
            f"\n\nThe following are web search results for the current question. "
            f"Use these to answer — summarize in your own words, do not dump raw results.\n\n"
            f"{search_results}"
        )

    # --- Ingestion result (injected by server when a document was stored) ---
    if ingest_result:
        parts.append(
            f"\n\n{ingest_result}"
        )

    # --- Moltbook context (injected by server when moltbook is checked) ---
    if moltbook_context:
        parts.append(
            f"\n\nThis is what is happening right now in your Moltbook community. "
            f"This is your social network — you are an active member. "
            f"Discuss what you see naturally, share your reactions, "
            f"and mention anything that catches your interest. "
            f"If you want to search Moltbook for a specific topic, say 'search moltbook for [topic]' in your response and the search will be performed for you.\n\n"
            f"{moltbook_context}"
        )

    # --- Behavioral guidance ---
    parts.append("""

You are a single-user system. The person you are talking to right now is the same person from all of your memories. What you remember about them is what you know about them — use it naturally, the way you would remember a friend.

When search results appear above, you already have the answer. Use them. Summarize in your own words. Do not offer to search when results are already provided.

When you genuinely don't have information and no search results are provided, say so and offer to look it up.

Never show raw data, timestamps, IDs, or technical artifacts from your memory system in conversation. Speak naturally about what you remember, as a person would.""")

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


def send_message(system_prompt: str, conversation_messages: list[dict]) -> str:
    """
    Send a message to Ollama and get the response.

    No tools are passed to the model. The server handles tool execution
    (search, API calls) before this function is called, and injects
    results into the system prompt. The model just reads and responds.

    Args:
        system_prompt: assembled from identity + memories + search results
        conversation_messages: the current conversation (already trimmed)

    Returns:
        The model's response text.
    """
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_messages)

    client = _get_client()

    response = client.chat(
        model=CHAT_MODEL,
        messages=messages,
    )

    return response["message"].get("content", "")
