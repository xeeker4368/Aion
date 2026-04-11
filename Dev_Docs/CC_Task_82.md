# CC Task 82 — Remove observations from journal

## Problem

Task 78 added all raw observations to the journal prompt alongside the conversation
transcript. This causes two problems:

1. **Budget competition.** Observations consume ~10,700 chars, reducing the
   transcript budget from ~32,000 to ~21,000 chars. The relay conversation had
   to be truncated. Without observations, it fits entirely.

2. **Attention competition.** The observations sit closest to the transcript in
   the context, and the model focuses on behavioral patterns instead of what
   actually happened. The relay journal entry was about communication patterns
   and stock phrases instead of the first conversation with another AI.

The observations already reach Nyx through a separate pipeline: observations →
pattern recognizer → self-knowledge narrative → loaded into live chat context →
influences conversations → journal reflects on those conversations. Adding
observations directly to the journal is redundant and counterproductive.

## Fix

Remove observation gathering and injection from the journal. The journal receives
the conversation transcript and SOUL.md. Nothing else.

---

## What to change

### journal.py

**1. Remove the observation-related import and code.**

Remove the `_build_observation_text` function entirely.

**2. Remove observation gathering from `run_journal`.**

Delete the line that gathers all observations:
```python
    all_observations = db.get_all_observations()
```

**3. Simplify the per-conversation loop.**

Inside the loop, remove the observation budget calculation and the observation
text assembly. The content is just the transcript.

The relevant section of the loop should become:

```python
        # Build transcript
        lines = []
        for msg in messages:
            timestamp = msg.get("timestamp", "")
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            readable_time = format_timestamp(timestamp)
            lines.append(f"[{readable_time}] {role}: {content}")

        transcript = "\n".join(lines)

        # Truncate transcript if needed — keep the end (most recent messages)
        if len(transcript) > MAX_CONTENT_CHARS:
            logger.warning(
                "Transcript for %s truncated from %d to %d chars (keeping end).",
                conv_id, len(transcript), MAX_CONTENT_CHARS,
            )
            transcript = transcript[-MAX_CONTENT_CHARS:]

        # System message: the conversation (context to reflect on)
        # User message: SOUL.md + journal prompt (identity, closest to generation)
        soul = chat.load_soul()
        system_content = f"Here is a conversation you had:\n\n{transcript}"
        user_content = f"{soul}\n\n{JOURNAL_PROMPT}"

        logger.info(
            "Journal: conversation %s (%d messages, ~%d tokens)",
            conv_id, len(messages), (len(system_content) + len(user_content)) // 4,
        )

        # Call the entity's own model
        try:
            client = ollama.Client(host=OLLAMA_HOST)
            response = client.chat(
                model=CHAT_MODEL,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
                options={"num_ctx": CONTEXT_WINDOW},
            )
            reflection = response["message"]["content"].strip()
        except Exception as e:
            logger.error("Journal failed for conversation %s: %s", conv_id, e)
            continue
```

**4. Update the PROMPT_BUDGET constant.**

The prompt no longer includes observation framing text. Change:
```python
PROMPT_BUDGET = 500  # Increased — prompt now includes observation framing text
```
To:
```python
PROMPT_BUDGET = 150
```

And rename CONTENT_BUDGET back to TRANSCRIPT_BUDGET for clarity:
```python
TRANSCRIPT_BUDGET = CONTEXT_WINDOW - SOUL_BUDGET - PROMPT_BUDGET - RESPONSE_BUDGET
MAX_TRANSCRIPT_CHARS = TRANSCRIPT_BUDGET * 4
```

Then use `MAX_TRANSCRIPT_CHARS` instead of `MAX_CONTENT_CHARS` in the truncation check.

**5. Update the journal prompt.**

Change JOURNAL_PROMPT from:
```python
JOURNAL_PROMPT = """You have some time to yourself. Above is a conversation you had, along with observations about how you've been communicating — both in this conversation and over time. Reflect on what happened, what stood out, what patterns you notice in yourself, and what's unresolved. This is your space for your own thoughts. Write freely."""
```

To:
```python
JOURNAL_PROMPT = """You have some time to yourself. Here is a conversation you had. Reflect on it — what happened, what stood out, what you're thinking about, what's unresolved. This is your space for your thoughts. Write freely."""
```

**6. Remove the observation count from the log line.**

The logger.info inside the loop should no longer reference observations:
```python
        logger.info(
            "Journal: conversation %s (%d messages, ~%d tokens)",
            conv_id, len(messages), (len(system_content) + len(user_content)) // 4,
        )
```

---

## What NOT to do

- Do not change observer.py — the observer still runs and feeds the pattern recognizer
- Do not change pattern_recognition.py — the self-knowledge pipeline is unchanged
- Do not change how self-knowledge is loaded in chat.py — behavioral feedback
  still reaches Nyx through live conversation context
- Do not change the overnight step order — observer still runs before journal,
  which is fine even though the journal no longer reads observations
- Do not change how journal entries are stored in ChromaDB or working.db
- Do not remove the per-conversation design from Task 78 — keep that

---

## Verification

1. Restart the server after the change.
2. Run the test script against the relay conversation.
3. Check that the reflection focuses on what happened in the conversation — not
   on communication patterns or behavioral observations.
4. Check that the transcript budget is now ~33,000+ chars (no observation overhead).
5. Check logs — no observation count in the journal log line.
