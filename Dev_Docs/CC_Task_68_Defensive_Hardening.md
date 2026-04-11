# CC Task 68 — Defensive Hardening (Code Review Findings)

Read this spec. Make exactly these changes. Nothing else.

## Overview

Five targeted fixes from the code review. No architecture changes. All defensive.

---

## Change 1: memory.py — Error handling in ingest_document()

Wrap the upsert loop so a failed chunk doesn't silently produce a wrong count.

Replace the for loop inside `ingest_document()` (the loop that iterates over chunks and calls `collection.upsert`) with:

```python
    successful = 0
    for i, chunk_text_piece in enumerate(chunks):
        # Prepend title to first chunk for better embedding context
        text_to_store = chunk_text_piece
        if i == 0:
            text_to_store = f"{title}\n\n{chunk_text_piece}"

        chunk_id = f"{doc_id}_chunk_{i}"

        try:
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
            successful += 1
        except Exception as e:
            import logging
            logging.getLogger("aion.memory").error(
                f"Failed to upsert chunk {i} for {doc_id}: {e}"
            )

    return successful
```

This returns the actual number of chunks that succeeded, not the total attempted.

---

## Change 2: chat.py — Error handling for missing soul.md

Replace the `load_soul()` function with:

```python
def load_soul() -> str:
    """Load SOUL.md content. Cached after first read."""
    global _soul_text
    if _soul_text is None:
        if not SOUL_PATH.exists():
            logger.error(f"SOUL.md not found at {SOUL_PATH}. Entity has no identity.")
            _soul_text = ""
        else:
            _soul_text = SOUL_PATH.read_text()
    return _soul_text
```

This logs a clear error instead of crashing with a raw FileNotFoundError. The entity runs without identity (empty string) rather than taking down the entire server. Same fix covers H-5 and H-11 (journal.py calls `chat.load_soul()`).

---

## Change 3: vault.py — Log warning on decryption failure

Find the `_load_secrets()` function. Find the bare `except Exception` that catches decryption failures (around line 76-78). It currently looks like:

```python
        except Exception:
            return {}
```

Replace with:

```python
        except Exception as e:
            import logging
            logging.getLogger("aion.vault").warning(
                f"Failed to decrypt secrets file: {e}. Starting with empty secrets. "
                f"If this persists, the master key may be wrong."
            )
            return {}
```

---

## Change 4: observer.py — Error handling for ChromaDB ingestion

In `run_observer()`, find where `memory.ingest_document()` is called for the observation (around line 129-135). Wrap it in try-except:

Replace:

```python
        # Store in ChromaDB
        doc_id = f"observation_{conv_id}"
        memory.ingest_document(
            doc_id=doc_id,
            text=f"Behavioral observation:\n\n{characterization}",
            title=f"Personality observation",
            source_type="observation",
            source_trust="secondhand",
        )
```

With:

```python
        # Store in ChromaDB
        doc_id = f"observation_{conv_id}"
        try:
            memory.ingest_document(
                doc_id=doc_id,
                text=f"Behavioral observation:\n\n{characterization}",
                title="Personality observation",
                source_type="observation",
                source_trust="secondhand",
            )
        except Exception as e:
            logger.error(
                "Failed to store observation for %s in ChromaDB: %s. "
                "Observation saved to DB2 but not searchable.",
                conv_id, e,
            )
```

The observation is already saved in DB2 before this point. If ChromaDB fails, the observation still exists for the profile generator — it's just not searchable by the entity. Log the error, don't crash.

---

## Change 5: db.py — Add database indexes

Inside `init_databases()`, after the existing index creation for archive (after `idx_archive_conversation`), add:

```python
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_archive_timestamp
            ON messages(timestamp)
        """)
```

Inside the working database section, after the existing `idx_working_conversation` index, add:

```python
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_started
            ON conversations(started_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_ended
            ON conversations(ended_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_working_timestamp
            ON messages(timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_source_type
            ON documents(source_type)
        """)
```

These are all `CREATE INDEX IF NOT EXISTS` — safe to run on existing databases. They'll be created on next startup.

---

## What NOT to Do

- Do NOT add locking, mutexes, or synchronization to the global conversation ID. Single-user system.
- Do NOT change the dual-database write pattern. Archive-first commit is architecturally sound.
- Do NOT add SSRF protection, URL validation, or path traversal guards. The only user is Lyle.
- Do NOT change the conversation_id metadata key naming. It's consistent across the codebase.
- Do NOT purge messages from working.db. Principle 6: never delete.
- Do NOT add Ollama timeouts yet — that needs design discussion about what to do when a timeout occurs.

## Verification

1. **ingest_document**: Upload a file through the UI. Check server logs for any upsert errors. The returned chunk count should match actual stored chunks.
2. **soul.md**: Temporarily rename soul.md, start the server, send a message. Should get an empty response (no identity) with a clear error in the logs, NOT a crash. Rename soul.md back.
3. **vault**: Corrupt the `.master_key` file (change a character), start the server. Should see a warning in logs about decryption failure, not a silent empty state. Fix the key after.
4. **observer ChromaDB**: Hard to test in isolation. The try-except ensures the overnight doesn't crash if ChromaDB has issues.
5. **indexes**: Start the server. Run `sqlite3 data/prod/working.db ".indexes"` — should list the new indexes.
