# CC Task 83 — Fix SOUL.md positioning in journal

## Problem

Task 82 placed SOUL.md in the user message and the transcript in the system
message. This caused the model to treat SOUL.md as something to respond to
rather than as identity anchoring. The journal output was about "fitting into
a predefined role" — reflecting on SOUL.md's content instead of the conversation.

Testing confirmed the correct positioning: SOUL.md at the END of the system
message (after the transcript), with just the journal prompt in the user message.
This mirrors how live chat works — SOUL.md at the bottom of the system prompt,
closest to the generation boundary, anchoring identity without becoming the topic.

---

## What to change

### journal.py

Find where the system and user messages are assembled in the per-conversation
loop. Currently (from Task 82) it looks like:

```python
        system_content = f"Here is a conversation you had:\n\n{transcript}"
        user_content = f"{soul}\n\n{JOURNAL_PROMPT}"
```

Change to:

```python
        system_content = f"Here is a conversation you had:\n\n{transcript}\n\n{soul}"
        user_content = JOURNAL_PROMPT
```

That's it. SOUL.md moves from the user message to the end of the system message.
The user message becomes just the journal prompt.

---

## What NOT to do

- Do not change anything else in journal.py
- Do not change the journal prompt wording
- Do not change SOUL.md
- Do not change any other file

---

## Verification

1. Restart the server.
2. The next overnight journal should produce reflections that reference actual
   conversation events, correctly identify persistence, and do not treat SOUL.md
   content as the subject of reflection.
