# CC Task 47: Fix chunk_count Metadata in journal.py and research.py

**Priority:** 3 (after chunk index fix and search limiter wiring)
**Risk:** Low — metadata accuracy fix, no behavioral change
**Files to modify:** journal.py, research.py

---

## The Problem

Both `journal.py` and `research.py` call `memory.ingest_document()` which returns the actual number of chunks created, but they ignore the return value and hardcode `chunk_count=1` when calling `db.save_document()`. This means the documents table in DB2 shows incorrect chunk counts for journals and research entries.

This matters because the UI (memory browser, dashboard) will eventually display chunk counts, and incorrect metadata erodes trust in the system.

---

## The Fix

### journal.py

**Current code (lines 97–112):**
```python
    memory.ingest_document(
        doc_id=doc_id,
        text=reflection,
        title=f"Journal — {today}",
        source_type="journal",
        source_trust="firsthand",
    )

    # Record in DB2 for UI
    db.save_document(
        doc_id=doc_id,
        title=f"Journal — {today}",
        source_type="journal",
        source_trust="firsthand",
        chunk_count=1,
    )
```

**Replace with:**
```python
    chunk_count = memory.ingest_document(
        doc_id=doc_id,
        text=reflection,
        title=f"Journal — {today}",
        source_type="journal",
        source_trust="firsthand",
    )

    # Record in DB2 for UI
    db.save_document(
        doc_id=doc_id,
        title=f"Journal — {today}",
        source_type="journal",
        source_trust="firsthand",
        chunk_count=chunk_count,
    )
```

### research.py

**Current code (lines 137–151):**
```python
    memory.ingest_document(
        doc_id=doc_id,
        text=research_record,
        title=f"Research — {today}",
        source_type="research",
        source_trust="secondhand",
    )

    db.save_document(
        doc_id=doc_id,
        title=f"Research — {today}",
        source_type="research",
        source_trust="secondhand",
        chunk_count=1,
    )
```

**Replace with:**
```python
    chunk_count = memory.ingest_document(
        doc_id=doc_id,
        text=research_record,
        title=f"Research — {today}",
        source_type="research",
        source_trust="secondhand",
    )

    db.save_document(
        doc_id=doc_id,
        title=f"Research — {today}",
        source_type="research",
        source_trust="secondhand",
        chunk_count=chunk_count,
    )
```

---

## What NOT to Do

- Do NOT change memory.ingest_document() — it already returns chunk count correctly.
- Do NOT change db.save_document() — its signature is fine.
- Do NOT touch observer.py — it uses db.save_observation(), not db.save_document(). Different table, different schema. No chunk_count column.
- Do NOT change any other logic in journal.py or research.py.

---

## Verification

1. Run the overnight cycle manually: `python overnight.py`
2. After it completes, check the documents table:
   ```sql
   sqlite3 data/working.db "SELECT id, title, source_type, chunk_count FROM documents ORDER BY created_at DESC LIMIT 10;"
   ```
3. Confirm that journal and research entries show actual chunk counts (likely 1 for short entries, but could be higher for longer research).
4. Compare: run `memory.ingest_document()` on a test string longer than 1500 chars — it should return >1 chunks. Verify that value matches what's in the documents table.
