# CC Task 61 — Code Cleanup (Debug Prints, Dead Code, Config Fix, Dead Route)

Read this spec. Make exactly these changes. Nothing else.

## Overview

Four small cleanup items. No architecture changes. No behavioral changes. All zero-risk.

---

## Change 1: memory.py — Remove debug prints from `ingest_document()`

Remove the `import os` at line 203, the `mem_mb()` helper function (lines 205-210), and all six `print(f"[UPLOAD DEBUG]...")` lines (lines 212, 217, 227, 232 area — see below).

The function should look like this after cleanup:

```python
def ingest_document(doc_id: str, text: str, title: str,
                    source_type: str = "article",
                    source_trust: str = "thirdhand") -> int:
    """
    Chunk and embed a document into ChromaDB.

    Documents are stored as clean text — no message wrapping,
    no fake timestamps, no role prefixes. They are not conversations.

    Returns the number of chunks created.
    """
    from config import INGEST_CHUNK_SIZE, INGEST_CHUNK_OVERLAP

    collection = _get_collection()
    chunks = chunk_text(text, INGEST_CHUNK_SIZE, INGEST_CHUNK_OVERLAP)

    for i, chunk_text_piece in enumerate(chunks):
        # Prepend title to first chunk for better embedding context
        text_to_store = chunk_text_piece
        if i == 0:
            text_to_store = f"{title}\n\n{chunk_text_piece}"

        chunk_id = f"{doc_id}_chunk_{i}"

        collection.upsert(
            ids=[chunk_id],
            documents=[text_to_store],
            metadatas=[{
                "conversation_id": doc_id,
                "chunk_index": i,
                "message_count": 0,
                "source_type": source_type,
                "source_trust": source_trust,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }],
        )

    return len(chunks)
```

---

## Change 2: memory.py — Remove dead `return chunks` at line 188

The `chunk_text()` function has a duplicate `return chunks` statement at line 188, after the real return at line 186. Delete line 188 (the second `return chunks`).

After this change, line 186 (`return chunks`) should be followed by two blank lines and then the `def ingest_document` line.

---

## Change 3: config.py — Fix INGEST_CHUNK_SIZE and INGEST_CHUNK_OVERLAP

Replace lines 69-71:

```python
# --- Document Ingestion ---
INGEST_CHUNK_SIZE = 1500
INGEST_CHUNK_OVERLAP = 200
```

With:

```python
# --- Document Ingestion ---
INGEST_CHUNK_SIZE = _overrides.get("INGEST_CHUNK_SIZE", 3000)
INGEST_CHUNK_OVERLAP = _overrides.get("INGEST_CHUNK_OVERLAP", 300)
```

This makes these values actually read from config.json (matching what config_manager.py already allows editing) and fixes the defaults to 3000/300 as intended.

---

## Change 4: server.py — Remove dead `/settings` route

Delete lines 666-669:

```python
@app.get("/settings")
async def serve_settings():
    """Serve the settings page."""
    return FileResponse(str(STATIC_DIR / "settings.html"))
```

The `settings.html` file was deleted in Session 15 when settings were integrated into the main SPA. This route would 404 if hit. The section comment above it (lines 662-664) can stay — the secrets endpoints below it still exist.

---

## What NOT to Do

- Do NOT change any other code in memory.py, config.py, or server.py.
- Do NOT change config_manager.py (it's already correct).
- Do NOT modify the `chunk_text()` function logic — only remove the dead return line.
- Do NOT add any new logging or print statements.

## Verification

1. **memory.py debug prints**: Upload a file through the UI. Check server console — no `[UPLOAD DEBUG]` lines should appear.
2. **memory.py dead return**: `python3 -c "import memory"` should import without errors.
3. **config.py**: Run `python3 -c "from config import INGEST_CHUNK_SIZE, INGEST_CHUNK_OVERLAP; print(INGEST_CHUNK_SIZE, INGEST_CHUNK_OVERLAP)"` — should print `3000 300` with no config.json override.
4. **server.py**: Start the server, visit `/settings` — should return 404 (not a server error about missing file).
