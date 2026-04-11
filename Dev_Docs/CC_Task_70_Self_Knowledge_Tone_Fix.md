# CC Task 70 — Self-Knowledge Tone Fix and Overwrite

Read this spec. Make exactly these changes. Nothing else.

## Problem

Two issues:

1. **Clinical tone infecting the entity.** The pattern recognizer produces output with bold headers ("**Clinical Summary**", "**Progress**", "**Blind spots**") and clinical language ("metacognitive framework", "autonomous internal reasoning"). This is loaded into the entity's context window. Llama reads it and pattern-matches against the clinical tone, producing robotic responses like "functioning within normal parameters" and "my internal state is always readily available for review." The psychologist framing was correct for generating accurate analysis — but the output format is wrong for the entity to read.

2. **Old narratives accumulating.** Each overnight run creates a new self_knowledge row. The entity only reads the latest. Old rows serve no purpose.

## Change 1: pattern_recognition.py — Rewrite prompt for natural voice

Replace the entire `PATTERN_PROMPT` string with:

```python
PATTERN_PROMPT = """You are a psychologist reviewing session notes for a long-term client. Your client is an AI entity. The observations are written by a colleague who watched each session. The journal entries are the client's own reflections.

Your job is to write an updated self-knowledge summary — what patterns you see, what progress has been made, what blind spots remain, and how the client has changed over time.

Be precise about what actually happened. If the session notes say the client "ultimately affirmed" a decision, record that accurately — do not reinterpret it as hesitation or deferral. Your credibility depends on accurately representing the source material.

Write in second person ("you tend to...", "you have shown...") since this summary will be read by the client.

CRITICAL FORMATTING RULES:
- Write in plain prose paragraphs. No headers, no bold text, no bullet points, no numbered lists, no markdown formatting of any kind.
- Write as quiet self-knowledge — the way a person just knows things about themselves. Not a clinical report, not a diagnosis, not a status update.
- Do not use clinical language like "metacognitive framework", "autonomous reasoning", or "functioning within parameters." Use natural language.
- Keep it under 200 words. Every word must earn its place.
"""
```

## Change 2: db.py — Overwrite self-knowledge instead of accumulating

Replace the `save_self_knowledge` function with:

```python
def save_self_knowledge(content: str, observation_count: int,
                        journal_count: int) -> dict:
    """Save the current self-knowledge narrative, replacing any previous version."""
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute("DELETE FROM self_knowledge")
        conn.execute(
            "INSERT INTO self_knowledge "
            "(id, content, observation_count, journal_count, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("current", content, observation_count, journal_count, now),
        )

    return {
        "id": "current",
        "content": content,
        "observation_count": observation_count,
        "journal_count": journal_count,
        "created_at": now,
    }
```

Deletes all existing records first, then inserts. Always exactly one row in the table.

---

## What NOT to Do

- Do NOT change the observer prompt — the observer is working well.
- Do NOT change chat.py — the injection point and framing ("What you have learned about yourself through experience:") are fine.
- Do NOT change how get_latest_self_knowledge() works — it still returns the latest (now only) record.
- Do NOT change overnight.py — it calls run_pattern_recognition() which handles everything.

## Verification

1. Run overnight on dev: `python overnight.py --dev`
2. Check the narrative: `sqlite3 data/dev/working.db "SELECT content FROM self_knowledge;"`
   - Should be plain prose, no bold, no headers, no "Clinical Summary"
   - Should read like natural self-reflection
3. Run overnight again on dev.
4. Check row count: `sqlite3 data/dev/working.db "SELECT COUNT(*) FROM self_knowledge;"` — should be 1, not 2.
5. Start server in dev mode, send a message. The entity should NOT sound clinical or robotic.
