# CC Task 5: Topic-Triggered Retrieval

## What This Is

Right now, every message triggers ChromaDB search and summary loading — including greetings. "Hey" gets the same retrieval treatment as "What do you remember about the Aion project?" This wastes tokens and retrieves irrelevant context.

The fix: detect trivial messages (greetings, confirmations, short filler) and skip retrieval for them. Substantive messages still get full retrieval.

Per the architecture: "Greetings and trivial messages get SOUL.md + identity context + conversation history. No ChromaDB search. No summaries."

## Changes

All changes are in `server.py`.

### Add the greeting detector function

Add this function near the other helper functions (after `_extract_search_query` and before the chat endpoints section):

```python
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
```

### Wrap the retrieval block in a conditional

Find the retrieval section in `handle_chat()`. It currently looks like this (step 3):

```python
    # 3. Retrieve memories
    retrieved_chunks = memory.search(
        query=request.message,
        exclude_conversation_id=conversation_id,
    )
    summaries = db.get_recent_summaries(limit=5)
```

Replace it with:

```python
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
```

Note: this replaces the existing retrieval block AND the existing log lines that follow it (the `logger.info` with chunk counts and the distance logging loop). Those are now inside the `else` branch.

### Add retrieval_skipped to debug logging

In the `request_data` dict inside `handle_chat()`, add this field (put it near the chunks/summaries fields):

```python
        "retrieval_skipped": _is_trivial_message(request.message),
```

### Update debug console format

In `debug.py`, in the `log_request` function, update the Context line to show when retrieval was skipped. Change:

```python
        f'Context: SOUL={d["soul_tokens"]} Chunks={d["chunks_count"]}({d["chunks_tokens"]}t) Summaries={d["summaries_count"]}({d["summaries_tokens"]}t) Skills={d["skills_tokens"]}t',
```

To:

```python
        f'Context: SOUL={d["soul_tokens"]} Chunks={d["chunks_count"]}({d["chunks_tokens"]}t) Summaries={d["summaries_count"]}({d["summaries_tokens"]}t) Skills={d["skills_tokens"]}t{" [RETRIEVAL SKIPPED]" if d.get("retrieval_skipped") else ""}',
```

## What NOT To Do

- Do NOT change the search tool gating (`_should_offer_tools`). That's a separate system for web search, not memory retrieval.
- Do NOT change how `build_system_prompt()` works in chat.py. It already handles empty lists gracefully.
- Do NOT change anything in memory.py or db.py.
- Do NOT add word-level analysis, NLP, or anything complex to the greeting detector. Simple pattern matching is correct here. It will be tuned over time based on what the debug log reveals.

## Verification

1. Restart the server.
2. Send "hey" in the chat UI.
3. Console should show `Retrieval: SKIPPED (trivial message)` and `[RETRIEVAL SKIPPED]` in the context line.
4. Chunks count should be 0, summaries count should be 0.
5. Entity should still respond normally using SOUL.md and conversation history.
6. Send "What do you think about persistent memory for AI systems?"
7. Console should show normal retrieval (no SKIPPED message). Chunks and summaries should be retrieved (counts may be 0 if database is empty, but the search should fire).
8. Send "thanks"
9. Console should show retrieval skipped again.

## Done When

Greetings skip retrieval, substantive messages trigger it, debug log clearly shows which path was taken.
