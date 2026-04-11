# CC Task 69 — Store Document Content in Working DB

Read this spec. Make exactly these changes. Nothing else.

## Problem

Journal text, research text, and uploaded file content exist ONLY in ChromaDB. Working.db has metadata (title, date, chunk_count) but not the actual content. If ChromaDB is corrupted or lost, journals, research, and uploaded documents are gone permanently and cannot be rebuilt.

The architecture says "if the working store gets corrupted, rebuild it from the archive." But the archive only has messages. It cannot rebuild journals, research, or uploaded files.

## Fix

Add a `content` column to the documents table. Store the full text alongside the metadata. Then ChromaDB can always be rebuilt from working.db + archive.db.

---

## Change 1: db.py — Add content column via migration

In `_migrate_working_db()`, after the existing `summary` migration (after line 138), add:

```python
    if "content" not in doc_columns:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN content TEXT"
        )
```

Note: `doc_columns` is already populated on line 134 from `PRAGMA table_info(documents)`. The new check uses the same set.

---

## Change 2: db.py — Update save_document() to accept and store content

Replace the `save_document` function (lines 329-339) with:

```python
def save_document(doc_id: str, title: str, url: str = None,
                  source_type: str = "article", source_trust: str = "thirdhand",
                  chunk_count: int = 0, content: str = None):
    """Record an ingested document with its full content for rebuild safety."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, url, source_type, source_trust, "
            "chunk_count, content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, title, url, source_type, source_trust, chunk_count, content, now),
        )
```

---

## Change 3: journal.py — Pass reflection text to save_document

Replace lines 107-113:

```python
    db.save_document(
        doc_id=doc_id,
        title=f"Journal — {today}",
        source_type="journal",
        source_trust="firsthand",
        chunk_count=chunk_count,
    )
```

With:

```python
    db.save_document(
        doc_id=doc_id,
        title=f"Journal — {today}",
        source_type="journal",
        source_trust="firsthand",
        chunk_count=chunk_count,
        content=reflection,
    )
```

---

## Change 4: research.py — Pass research text to save_document

Replace lines 165-171:

```python
    db.save_document(
        doc_id=doc_id,
        title=f"Research — {today}",
        source_type="research",
        source_trust="secondhand",
        chunk_count=chunk_count,
    )
```

With:

```python
    db.save_document(
        doc_id=doc_id,
        title=f"Research — {today}",
        source_type="research",
        source_trust="secondhand",
        chunk_count=chunk_count,
        content=research_record,
    )
```

---

## Change 5: server.py — Pass content to save_document in URL ingestion

Replace lines 416-423:

```python
    db.save_document(
        doc_id=doc_id,
        title=title,
        url=url,
        source_type="article",
        source_trust="thirdhand",
        chunk_count=chunk_count,
    )
```

With:

```python
    db.save_document(
        doc_id=doc_id,
        title=title,
        url=url,
        source_type="article",
        source_trust="thirdhand",
        chunk_count=chunk_count,
        content=content,
    )
```

---

## Change 6: server.py — Pass content to save_document in file upload

Replace lines 825-831:

```python
    db.save_document(
        doc_id=doc_id,
        title=title,
        source_type=source_type,
        source_trust=source_trust,
        chunk_count=chunk_count,
    )
```

With:

```python
    db.save_document(
        doc_id=doc_id,
        title=title,
        source_type=source_type,
        source_trust=source_trust,
        chunk_count=chunk_count,
        content=text,
    )
```

---

## Change 7: executors.py — Pass content to save_document in store_document executor

Replace lines 246-252:

```python
    db.save_document(
        doc_id=doc_id,
        title=title,
        source_type=doc_type,
        source_trust=source_trust,
        chunk_count=chunk_count,
    )
```

With:

```python
    db.save_document(
        doc_id=doc_id,
        title=title,
        source_type=doc_type,
        source_trust=source_trust,
        chunk_count=chunk_count,
        content=content,
    )
```

---

## What NOT to Do

- Do NOT change the documents table CREATE TABLE statement — the migration handles existing databases.
- Do NOT change memory.py or ChromaDB storage — this is additive redundancy, not a replacement.
- Do NOT change how documents are read or displayed — existing code that reads documents doesn't need the content column.
- Do NOT backfill content for existing documents — they already exist in ChromaDB. Future documents get the safety net.

## Verification

1. Start the server. Upload a file through the UI.
2. Check working.db: `sqlite3 data/prod/working.db "SELECT id, title, length(content) FROM documents ORDER BY created_at DESC LIMIT 1;"` — should show the document with a non-null content length.
3. Run overnight on dev. Check that journal and research entries have content stored: `sqlite3 data/dev/working.db "SELECT title, length(content) FROM documents WHERE content IS NOT NULL;"`
4. Existing documents (from before this change) will have `content = NULL`. That's expected — they're already in ChromaDB.
