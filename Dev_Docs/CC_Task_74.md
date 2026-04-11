# CC Task 74 — Truncate chunk text before embedding to prevent context overflow

## Problem

When a live chunk is created from a long conversation, the chunk text can exceed
nomic-embed-text's context window. Ollama returns HTTP 400, ChromaDB throws
`ResponseError: the input length exceeds the context length`, and the server
returns a 500 error. The conversation remains live but the chunk is not saved
to ChromaDB.

Root location: `memory.py` → `create_live_chunk()`

---

## What to change

### memory.py

Find `create_live_chunk()`. Before the `collection.upsert()` call, add a
truncation step that ensures the chunk text does not exceed a safe character
limit for the embedding model.

Add this constant near the top of the file where other config values are
imported or defined:

```python
EMBED_MAX_CHARS = 8000
```

Then in `create_live_chunk()`, immediately before the upsert, add:

```python
if len(chunk_text) > EMBED_MAX_CHARS:
    logger.warning(f"Chunk text truncated from {len(chunk_text)} to {EMBED_MAX_CHARS} chars before embedding.")
    chunk_text = chunk_text[:EMBED_MAX_CHARS]
```

Apply the same truncation in any other location in `memory.py` where text is
passed to the embedding function — specifically `ingest_document()` and any
other method that calls `collection.upsert()` or `collection.add()` with
document text. Check each call site and add the same guard.

---

## What NOT to do

- Do not change the chunk size configuration — this is a safety truncation
  only, not a chunk size change
- Do not truncate the text stored in working.db — only truncate what is passed
  to the embedding function
- Do not raise an exception or skip the upsert — truncate and continue
- Do not change anything in overnight.py, observer.py, or any other file
- Do not change INGEST_CHUNK_SIZE or EMBED_CHUNK_OVERLAP config values

---

## Verification

1. Restart the server after the change
2. Have a long conversation until live chunking triggers
3. Confirm no 500 error and no `input length exceeds context length` in logs
4. Confirm the chunk appears in ChromaDB
5. Check the log for the truncation warning if the chunk was long enough to
   trigger it
