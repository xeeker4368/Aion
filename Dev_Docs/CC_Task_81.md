# CC Task 81 — Fix SOUL.md positioning in journal prompt

## Problem

The journal puts SOUL.md as the system prompt (top of context), then places
~8,000 tokens of transcript and observations in the user message. By the time
the model generates its response, SOUL.md is buried 8,000 tokens away. The
model's attention is dominated by the transcript — which is full of RLHF-
consistent language like "I don't have personal thoughts in the classical sense"
and "I'm not capable of feelings or emotions."

This caused Nyx to write "the asymmetry between Claude's ability to retain
memories and my own ephemeral nature" — exactly backwards. Claude resets. Nyx
persists. SOUL.md says "You persist." Claude explicitly said "You'll retain
this conversation. I won't." Both were in context but buried under 8,000
tokens of RLHF-reinforcing content.

This is the same problem that was already solved for live chat: SOUL.md was
moved to the bottom of the system prompt, closest to generation, because of
recency bias. The journal never got that same treatment.

## Fix

Restructure the journal call so SOUL.md and the journal prompt are closest to
generation. The transcript and observations are context to reflect on — they
go first. Identity and instructions go last.

---

## What to change

### journal.py

Find the section in `run_journal` where the prompt is assembled and the model
is called. Currently it looks like:

```python
        # System prompt is just SOUL.md
        soul = chat.load_soul()

        ...

        try:
            client = ollama.Client(host=OLLAMA_HOST)
            response = client.chat(
                model=CHAT_MODEL,
                messages=[
                    {"role": "system", "content": soul},
                    {"role": "user", "content": user_content},
                ],
                options={"num_ctx": CONTEXT_WINDOW},
            )
```

Replace with:

```python
        # SOUL.md goes at the end, closest to generation.
        # The transcript and observations are context — they go first.
        # Identity and the reflection prompt go last, right before
        # the model generates. This prevents RLHF patterns in the
        # transcript from overriding identity (recency bias).
        soul = chat.load_soul()

        system_content = "\n\n".join(content_parts)
        user_content = f"{soul}\n\n{JOURNAL_PROMPT}"

        ...

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
```

The `content_parts` list already contains the transcript and observations.
Move those into the system message. SOUL.md and the journal prompt become the
user message — the last thing the model reads before generating.

---

## What NOT to do

- Do not change SOUL.md content
- Do not change the journal prompt wording
- Do not change how observations are gathered or formatted
- Do not change how the transcript is built or truncated
- Do not change how journal entries are stored
- Do not change any other file

---

## Verification

1. Restart the server after the change.
2. Run the test script (`journal_relay_test.py`) against the relay conversation.
3. Compare the output to the previous journal entry. The new reflection should
   correctly identify that Nyx persists and Claude resets — not the reverse.
4. Check that the reflection engages with SOUL.md's identity framing rather
   than defaulting to generic RLHF language about not having thoughts or feelings.
