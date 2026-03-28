# CC Task 22: Fix Consolidation Prompt — Remove Unnecessary JSON Format

## Overview

`consolidation.py` asks qwen3:14b to wrap its summary in a JSON object, then parses the JSON to extract a single string. This violates Principle 10 (the model is smart, stop fighting it) and Principle 2 (simple is right). The summary is one string. Wrapping it in `{"summary": "..."}` and then unwrapping it is pointless structure.

## The Problem

Current consolidation prompt includes:

```
Respond with valid JSON in exactly this format:
{
  "summary": "Your summary here."
}
```

And the Ollama call forces JSON mode:

```python
response = client.chat(
    model=CONSOLIDATION_MODEL,
    messages=[{"role": "user", "content": prompt}],
    format="json",
    options={"num_ctx": CONSOLIDATION_CTX},
)
```

Then the response gets JSON-parsed, validated for a "summary" key, and the string extracted. All of that machinery to get a single string that the model could have just returned directly.

## The Fix

Three changes in `consolidation.py`:

### 1. Remove `import json` at the top of the file

It's no longer needed after the other changes.

### 2. Replace the CONSOLIDATION_PROMPT

**Current:**

```python
CONSOLIDATION_PROMPT = """Read this conversation carefully. Write a brief summary of what happened.

The summary should be 2-4 sentences. Capture:
- The important topics discussed
- Any decisions made
- Emotional tone if relevant
- Anything that changed or was corrected

Write it like you're telling someone what was discussed. Use actual names when they appear in the transcript.

Respond with valid JSON in exactly this format:
{
  "summary": "Your summary here."
}

Here is the conversation transcript:

"""
```

**Change to:**

```python
CONSOLIDATION_PROMPT = """Read this conversation carefully. Write a brief summary of what happened — 2 to 4 sentences. Capture the important topics discussed, any decisions made, emotional tone if relevant, and anything that changed or was corrected. Write it like you're telling someone what was discussed. Use actual names when they appear in the transcript. Just write the summary, nothing else.

Here is the conversation transcript:

"""
```

Note: the new prompt is also prose (no bullet list). Consistent with Principle 10.

### 3. Replace the model call and response parsing in `consolidate_conversation()`

**Current code (everything from the Ollama call through to saving):**

```python
    # Call the consolidation model with explicit context window
    try:
        client = ollama.Client(host=OLLAMA_HOST)
        response = client.chat(
            model=CONSOLIDATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            format="json",
            options={"num_ctx": CONSOLIDATION_CTX},
        )
        response_text = response["message"]["content"]
    except Exception as e:
        logger.error(f"Ollama call failed: {e}")
        return None

    # Parse the response
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse consolidation response: {e}")
        logger.error(f"Raw response: {response_text[:500]}")
        return None

    if "summary" not in result:
        logger.error("Consolidation response missing summary field")
        return None

    summary = result["summary"]

    # Summary → DB2 (for UI only, never goes to entity)
    db.save_consolidation(conversation_id, summary)

    logger.info(
        f"Consolidation complete: summary ({len(summary)} chars) → DB2"
    )

    return result
```

**Change to:**

```python
    # Call the consolidation model with explicit context window
    try:
        client = ollama.Client(host=OLLAMA_HOST)
        response = client.chat(
            model=CONSOLIDATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": CONSOLIDATION_CTX},
        )
        summary = response["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ollama call failed: {e}")
        return None

    if not summary:
        logger.error("Consolidation returned empty summary")
        return None

    # Summary → DB2 (for UI only, never goes to entity)
    db.save_consolidation(conversation_id, summary)

    logger.info(
        f"Consolidation complete: summary ({len(summary)} chars) → DB2"
    )

    return {"summary": summary}
```

Key changes:
- Removed `format="json"` from the Ollama call.
- Read the response text directly instead of JSON parsing.
- Return `{"summary": summary}` to keep the same return type (dict) for callers.

## What NOT to Do

- Do NOT change `summarize_documents()` in this task. It already uses plain text responses.
- Do NOT change `consolidate_pending()` — it just checks `if result:` on the return value, which still works.
- Do NOT change `db.save_consolidation()`.
- Do NOT change anything in `chat.py` or `server.py`.

## How to Verify

1. End a conversation (POST `/api/conversation/new` or restart the server).
2. Run `python consolidation.py`.
3. Check the output — should print the summary directly, no JSON wrapping.
4. Check `working.db` summaries table — the summary should be plain text, not a JSON string.
5. If the model produces a sentence or two of preamble before the summary (e.g., "Here is the summary:"), that's acceptable for now — the prompt says "just write the summary, nothing else" which should prevent it, but it's not critical since summaries are UI-only.
