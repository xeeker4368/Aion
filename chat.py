"""
Aion Chat Layer

Handles communication with Ollama and assembles what the model sees:
- SOUL.md (identity)
- Known facts (compact, high-importance knowledge)
- Retrieved conversation chunks (context, nuance)
- Recent summaries (big picture orientation)
- Available skills (what capabilities the entity has)
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


def _estimate_tokens(text: str) -> int:
    """Rough token estimate. 1 token ≈ 4 characters for English text."""
    return len(text) // 4


def load_soul() -> str:
    """Load SOUL.md content. Cached after first read."""
    global _soul_text
    if _soul_text is None:
        _soul_text = SOUL_PATH.read_text()
    return _soul_text


def build_system_prompt(
    retrieved_chunks: list[dict],
    facts: list[dict] = None,
    summaries: list[dict] = None,
    skill_descriptions: str = "",
    search_results: str = None,
) -> str:
    """
    Assemble the system prompt from SOUL.md and all memory layers.

    The model sees (in order):
    1. The soul (identity)
    2. Known facts (compact, high-importance — quick answers)
    3. Relevant conversation chunks (context, nuance)
    4. Recent summaries (big picture orientation)

    Each layer has a share of the retrieval token budget.
    Facts get priority because they're the most compact and useful.
    """
    soul = load_soul()
    parts = [soul]

    tokens_remaining = RETRIEVAL_TOKEN_BUDGET

    # --- Layer 1: Facts (compact, prioritized by importance) ---
    if facts:
        fact_texts = []
        for fact in facts:
            content = fact.get("content", "")
            fact_tokens = _estimate_tokens(content)
            if fact_tokens > tokens_remaining:
                break
            fact_texts.append(f"- {content}")
            tokens_remaining -= fact_tokens

        if fact_texts:
            facts_block = "\n".join(fact_texts)
            parts.append(
                f"\n\n## What You Know\n\n"
                f"These are things you have learned from your experiences:\n\n"
                f"{facts_block}"
            )

    # --- Layer 2: Conversation chunks (context and nuance) ---
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
                f"\n\n## Relevant Memories\n\n"
                f"The following are excerpts from your past conversations:\n\n"
                f"{chunks_block}"
            )

    # --- Layer 3: Summaries (big picture) ---
    if summaries:
        summary_texts = []
        for summary in summaries:
            content = summary.get("content", "")
            summary_tokens = _estimate_tokens(content)
            if summary_tokens > tokens_remaining:
                break
            summary_texts.append(content)
            tokens_remaining -= summary_tokens

        if summary_texts:
            summaries_block = "\n".join(f"- {s}" for s in summary_texts)
            parts.append(
                f"\n\n## Recent Conversations\n\n"
                f"Brief summaries of your recent conversations:\n\n"
                f"{summaries_block}"
            )

    # --- Skills ---
    if skill_descriptions:
        parts.append(f"\n\n{skill_descriptions}")

    # --- Search results (injected by server when relevant) ---
    if search_results:
        parts.append(
            f"\n\n## Search Results\n\n"
            f"The following are web search results relevant to the current question. "
            f"Summarize these in your own words — do not dump raw results.\n\n"
            f"{search_results}"
        )

    # --- Behavioral guidance ---
    parts.append("""

## How You Handle Information

Your memories and knowledge are your primary sources. When you don't know something about the external world (software, news, products, events), say so and offer to search. For example: "I don't have information on that. Would you like me to search for it?"

When search results are provided above, use them to answer the question. Summarize in your own words.

Questions about the person you're talking to should ALWAYS come from your memories, never from searching.

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
        system_prompt: assembled from SOUL.md + memories + search results
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

