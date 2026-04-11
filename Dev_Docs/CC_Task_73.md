# CC Task 73 — Skip duplicate research/journal on same-day re-run

## What to change

In `research.py` and `journal.py`, add a duplicate check before attempting to save. If a document with that date's ID already exists in working.db, log it and return without inserting.

---

## db.py

Add this function:

```python
def document_exists(self, doc_id: str) -> bool:
    with self.get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM documents WHERE id = ? LIMIT 1",
            (doc_id,)
        ).fetchone()
        return row is not None
```

---

## research.py

Find the section where `doc_id` is constructed for the research document. It will look something like:

```python
doc_id = f"research_{date}"
```

Immediately after that line, add:

```python
if db.document_exists(doc_id):
    logger.info(f"Research already stored for {date}, skipping.")
    return
```

---

## journal.py

Same pattern. Find where `doc_id` is constructed:

```python
doc_id = f"journal_{date}"
```

Immediately after that line, add:

```python
if db.document_exists(doc_id):
    logger.info(f"Journal already stored for {date}, skipping.")
    return
```

---

## What NOT to do

- Do not change how `doc_id` is constructed
- Do not catch the UNIQUE constraint exception as an alternative — check before insert, don't swallow errors
- Do not modify consolidation, observer, or any other overnight step
- Do not change the log level of any existing log lines

---

## Verification

Run overnight manually twice in a row on the same day. Second run should log:

```
Research already stored for [date], skipping.
Journal already stored for [date], skipping.
```

No exception, no crash. Observer and consolidation should still run normally on the second run.
