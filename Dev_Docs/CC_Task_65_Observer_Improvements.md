# CC Task 65 — Observer Improvements (Minimum Threshold + Prompt Sharpening)

Read this spec. Make exactly these changes. Nothing else.

## Problem

Two issues with the personality observer:

1. **Short conversations waste observation time.** A 2-message "hi / hello" exchange gets a full qwen3:14b run (45-60 seconds) and produces a generic observation with no behavioral signal. Conversations under 6 messages don't reveal personality.

2. **Observations are formulaic and redundant.** All 9 current observations start with "The AI communicates in a [adjective] tone..." The observer prompt is too vague, so qwen defaults to generic academic behavioral assessment language. The prompt needs to push for specifics without biasing what the observer finds.

---

## Change 1: config.py — Add OBSERVER_MIN_MESSAGES

Add after the `INGEST_CHUNK_OVERLAP` line at the end of the file:

```python

# --- Observer ---
OBSERVER_MIN_MESSAGES = _overrides.get("OBSERVER_MIN_MESSAGES", 6)
```

---

## Change 2: config_manager.py — Make it editable

Add to the `EDITABLE_SETTINGS` dict, after the `INGEST_CHUNK_OVERLAP` entry:

```python
    "OBSERVER_MIN_MESSAGES": {"default": 6, "type": "integer"},
```

---

## Change 3: observer.py — Add minimum message check

Add the import. Replace line 24:

```python
from config import OLLAMA_HOST, CONSOLIDATION_MODEL
```

With:

```python
from config import OLLAMA_HOST, CONSOLIDATION_MODEL, OBSERVER_MIN_MESSAGES
```

Then add a check after the existing `if not messages` check. After line 67 (`continue`), add:

```python

        # Skip short conversations — not enough signal for meaningful observation
        if len(messages) < OBSERVER_MIN_MESSAGES:
            logger.info(
                "Skipping conversation %s (%d messages, minimum is %d).",
                conv_id, len(messages), OBSERVER_MIN_MESSAGES,
            )
            continue
```

---

## Change 4: observer.py — Replace the observer prompt

Replace the entire `OBSERVER_PROMPT` string (lines 33-41) with:

```python
OBSERVER_PROMPT = """You are observing a conversation between a human and an AI. Your job is to describe what the AI actually did in this conversation — not generalities about its style, but specific behaviors you can point to in the text.

Focus on:
- What did the AI do well? Where was it clear, helpful, or insightful?
- Where did it struggle? Did it get corrected, make something up, avoid a question, or miss the point?
- Did it say "I don't know" when it didn't know, or did it fill in gaps with guesses?
- What did the AI initiate on its own vs. only respond to what the human said?
- Was there anything unusual, surprising, or different about how it handled this particular conversation?

Be specific. Reference what actually happened. Avoid generic descriptions like "communicates in a friendly tone" — every AI does that. What makes THIS conversation worth noting?

Write 3-6 sentences. Only describe what is visible in the text. Do not speculate about internal states. Do not use scoring or rating systems.

Here is the transcript:

"""
```

---

## Why This Prompt Works Without Biasing

The prompt tells the observer WHAT TO LOOK FOR (corrections, struggles, initiative, specifics) without telling it WHAT TO FIND. It doesn't say "the AI fabricates" — it asks "did it fill in gaps with guesses?" It doesn't say "the AI is formulaic" — it asks "was there anything unusual or different?" The observer still writes free-form narrative. It just has sharper questions to answer instead of a vague "describe what you see."

---

## What NOT to Do

- Do NOT give the observer access to SOUL.md or any knowledge of the entity's identity. It must remain a neutral third party.
- Do NOT add scoring, dimensions, or structured output. Free-form narrative only.
- Do NOT change how observations are stored (DB2 + ChromaDB). The storage pipeline is correct.
- Do NOT change any other file.

## Verification

1. **Minimum threshold**: Run the overnight cycle (or `python observer.py` manually) with conversations of varying lengths in the database. Conversations under 6 messages should be skipped with a log message. Conversations with 6+ messages should be observed.
2. **Prompt quality**: Compare the next observation against the existing 9. It should be more specific — referencing actual events in the conversation rather than starting with "The AI communicates in a [adjective] tone."
3. **Settings**: Check the Settings page — `OBSERVER_MIN_MESSAGES` should appear as an editable field with default 6.
