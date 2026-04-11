# CC Task 71 — Preserve Self-Knowledge History

Read this spec. Make exactly this change. Nothing else.

## Problem

`save_self_knowledge()` deletes all previous records before inserting. This violates Principle 6 (never delete, only layer) and destroys the entity's growth history. Old narratives show how self-knowledge evolved over time.

## Change: db.py — Remove DELETE, use unique IDs

Replace the `save_self_knowledge` function with:

```python
def save_self_knowledge(content: str, observation_count: int,
                        journal_count: int) -> dict:
    """Save an updated self-knowledge narrative. Old versions are kept as history."""
    sk_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT INTO self_knowledge "
            "(id, content, observation_count, journal_count, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sk_id, content, observation_count, journal_count, now),
        )

    return {
        "id": sk_id,
        "content": content,
        "observation_count": observation_count,
        "journal_count": journal_count,
        "created_at": now,
    }
```

## What This Does

Each overnight run adds a new record. `get_latest_self_knowledge()` already returns only the newest (`ORDER BY created_at DESC LIMIT 1`). The entity only ever sees the latest narrative in its context window. Old records are history for Lyle — viewable in the UI or via SQL.

## What NOT to Do

- Do NOT change `get_latest_self_knowledge()` — it already works correctly.
- Do NOT change pattern_recognition.py — it reads the previous narrative via `get_latest_self_knowledge()` before generating a new one, which still works.
- Do NOT change chat.py — it loads the latest, which is still the newest record.

## Verification

1. Run overnight twice on dev. Check: `sqlite3 data/dev/working.db "SELECT id, created_at, substr(content,1,40) FROM self_knowledge ORDER BY created_at;"` — should show 2 rows.
2. The entity should only see the latest one in its context window.
