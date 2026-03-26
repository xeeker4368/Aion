# CC Task 2: Remove SQL Facts Table

## What This Is

Facts are supposed to live only in ChromaDB (the vector index). Right now they also live in a SQL table in the working database, creating a duplication bug. This task removes the SQL copy. ChromaDB remains the single source for facts.

This is one logical change: stop using the SQL facts table. It touches four files but every edit serves that single purpose.

## Context You Need

- `consolidation.py` has a function `_embed_facts()` that writes facts to **ChromaDB**. This is CORRECT. Do not touch it.
- `db.save_consolidation()` writes facts to the **SQL table**. This is the duplication. Remove the SQL part.
- `server.py` reads facts from SQL via `db.get_facts_by_importance()`. This call needs to go. Facts in ChromaDB already surface through `memory.search()` results.

## Changes

### File 1: `db.py`

**Remove the facts table creation** from `init_databases()`. Delete these lines (approx lines 83-101):
```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                content TEXT NOT NULL,
                importance INTEGER DEFAULT 5,
                category TEXT DEFAULT 'general',
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_conversation
            ON facts(conversation_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_importance
            ON facts(importance DESC)
        """)
```

**Remove fact-writing from `save_consolidation()`**. The function should only save the summary and mark consolidated. Change it to:

```python
def save_consolidation(conversation_id: str, summary: str):
    """
    Save consolidation summary for a conversation.
    Marks the conversation as consolidated.
    Facts are written to ChromaDB separately — not here.
    """
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        # Save summary
        summary_id = str(uuid.uuid4())
        conn.execute(
            "INSERT OR REPLACE INTO summaries (id, conversation_id, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (summary_id, conversation_id, summary, now),
        )

        # Mark conversation as consolidated
        conn.execute(
            "UPDATE conversations SET consolidated = 1 WHERE id = ?",
            (conversation_id,),
        )
```

Note: the `facts` parameter is removed from the function signature.

**Delete the entire `get_facts_by_importance()` function** (approx lines 296-304):
```python
def get_facts_by_importance(min_importance: int = 1, limit: int = 50) -> list[dict]:
    ...
```
Remove it completely.

### File 2: `consolidation.py`

**Update the `db.save_consolidation()` call** (approx line 124). Change:
```python
    db.save_consolidation(conversation_id, summary, facts)
```
To:
```python
    db.save_consolidation(conversation_id, summary)
```

Do NOT touch `_embed_facts()`. It writes to ChromaDB, which is correct.

### File 3: `server.py`

**Remove the SQL facts retrieval** (approx line 332). Delete:
```python
    facts = db.get_facts_by_importance(min_importance=4, limit=30)
```

**Update the log line** (approx line 336-338). Change:
```python
    logger.info(
        f"Retrieved {len(retrieved_chunks)} chunks, "
        f"{len(facts)} facts, {len(summaries)} summaries"
    )
```
To:
```python
    logger.info(
        f"Retrieved {len(retrieved_chunks)} chunks, "
        f"{len(summaries)} summaries"
    )
```

**Remove `facts` from the `build_system_prompt()` call** (approx lines 374-379). Change:
```python
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        facts=facts,
        summaries=summaries,
        skill_descriptions=skill_desc,
        search_results=search_results,
    )
```
To:
```python
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        summaries=summaries,
        skill_descriptions=skill_desc,
        search_results=search_results,
    )
```

**Remove facts from debug logging** (approx lines 395-398). Delete:
```python
        "facts_count": len(facts),
        "facts_tokens": debug.estimate_tokens(
            "\n".join(f.get("content", "") for f in facts)
        ),
```

**Update `memories_used` in the response** (approx line 458). Change:
```python
        memories_used=len(retrieved_chunks) + len(facts),
```
To:
```python
        memories_used=len(retrieved_chunks),
```

**Update the debug console format in `debug.py`** — the `log_request` function references `facts_count` and `facts_tokens` in the console output string (approx line 167). Change:
```python
        f'Context: SOUL={d["soul_tokens"]} Facts={d["facts_count"]}({d["facts_tokens"]}t) Chunks={d["chunks_count"]}({d["chunks_tokens"]}t) Summaries={d["summaries_count"]}({d["summaries_tokens"]}t) Skills={d["skills_tokens"]}t',
```
To:
```python
        f'Context: SOUL={d["soul_tokens"]} Chunks={d["chunks_count"]}({d["chunks_tokens"]}t) Summaries={d["summaries_count"]}({d["summaries_tokens"]}t) Skills={d["skills_tokens"]}t',
```

### File 4: `chat.py`

**Remove the `facts` parameter and Layer 1 rendering** from `build_system_prompt()`. 

Remove `facts: list[dict] = None,` from the function signature.

Delete the entire Layer 1 block (approx lines 78-95):
```python
    # --- Layer 1: Facts (compact, prioritized by importance) ---
    if facts:
        fact_texts = []
        for fact in facts:
            content = fact.get("content", "")
            fact_tokens = _estimate_tokens(content)
            if fact_tokens > tokens_remaining:
                break
            fact_texts.append(f"- {content}")
            tokens_remaining -= fact_tokens

        if fact_texts:
            facts_block = "\n".join(fact_texts)
            parts.append(
                f"\n\n## What You Know\n\n"
                f"These are things you have learned from your experiences:\n\n"
                f"{facts_block}"
            )
```

Update the docstring to remove references to "known facts" as a separate layer. The function comment about layer ordering (lines 64-71) should be updated to reflect that facts now come through ChromaDB search results in the chunks.

## What NOT To Do

- Do NOT touch `_embed_facts()` in consolidation.py. It writes to ChromaDB. That's correct.
- Do NOT drop the facts table from the existing database file. The database cleanup is a separate task. The table will sit there unused until then.
- Do NOT change retrieval logic, chunking logic, or anything else.
- Do NOT add any new code. This task is purely removal.

## Verification

1. Restart the server: `python server.py`
2. Confirm startup banner shows no errors.
3. Send a message in the chat UI (e.g., "Hello").
4. Check console output — no errors, no references to "facts" in the per-request log.
5. Check `data/logs/debug.log` — confirm the full system prompt does NOT contain a "## What You Know" section.
6. Send a second message (e.g., "Tell me something about yourself").
7. Confirm the entity responds using SOUL.md identity and any ChromaDB search results — no crash, no missing function errors.

## Done When

Server starts clean, responds to messages, debug log shows no facts section in the system prompt, and no errors reference `get_facts_by_importance` or the facts table.
