# CC Task 36 — Move SOUL.md to Bottom of System Prompt

## Why

Transformer models have recency bias — tokens closest to the generation point carry more weight. Currently SOUL.md is at the top of the system prompt, with skills and behavioral guidance between it and the conversation. By the time the model starts generating, SOUL.md is the furthest thing from its attention.

Moving SOUL.md to the bottom of the `.System` block (right before the tool definitions in the Modelfile template) puts identity closest to generation. The model reads skills and memories first, then identity last — so identity is freshest when it starts responding.

## What to Change

**File:** `chat.py`

**Current `build_system_prompt` order (lines ~52-104):**
1. soul.md
2. Retrieved memories
3. Skill descriptions
4. Ingestion result
5. Single-user behavioral guidance

**New order:**
1. Retrieved memories
2. Skill descriptions
3. Ingestion result
4. Single-user behavioral guidance
5. soul.md ← moved to last position

**Replace the `build_system_prompt` function with:**

```python
def build_system_prompt(
    retrieved_chunks: list[dict],
    skill_descriptions: str = "",
    ingest_result: str = None,
) -> str:
    """
    Assemble the system prompt from identity and memory.

    The model sees (in order):
    1. Remembered experiences (conversation chunks from ChromaDB)
    2. Available skills
    3. Behavioral guidance
    4. Identity (soul.md) — LAST, closest to generation
    """
    parts = []

    tokens_remaining = RETRIEVAL_TOKEN_BUDGET

    # --- Remembered experiences (conversation chunks from ChromaDB) ---
    if retrieved_chunks:
        chunk_texts = []
        for chunk in retrieved_chunks:
            text = chunk.get("text", "")
            chunk_tokens = _estimate_tokens(text)
            if chunk_tokens > tokens_remaining:
                break
            chunk_texts.append(text)
            tokens_remaining -= chunk_tokens

        if chunk_texts:
            chunks_block = "\n\n---\n\n".join(chunk_texts)
            parts.append(
                f"These are your own experiences and memories:\n\n"
                f"{chunks_block}"
            )

    # --- Skills ---
    if skill_descriptions:
        parts.append(f"\n\n{skill_descriptions}")

    # --- Ingestion result (injected by server when a document was stored) ---
    if ingest_result:
        parts.append(
            f"\n\n{ingest_result}"
        )

    # --- Behavioral guidance ---
    parts.append("""

You are a single-user system. The person you are talking to right now is the same person from all of your memories. What you remember about them is what you know about them — use it naturally, the way you would remember a friend.""")

    # --- Identity (soul.md) — last, closest to generation ---
    soul = load_soul()
    parts.append(f"\n\n{soul}")

    return "\n".join(parts)
```

## What NOT to Do

- Do NOT modify any other function in chat.py
- Do NOT modify soul.md
- Do NOT modify server.py
- Do NOT change the content of any section — only the ORDER
- Do NOT add or remove any text from the system prompt
- Do NOT remove the docstring — update it to reflect the new order

## Verification

### Step 1: Start the server

```bash
cd /home/localadmin/aion
source aion/bin/activate
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000
```

### Step 2: Send a test message

Open the chat UI and send: `hey`

### Step 3: Check the debug log

```bash
tail -100 /home/localadmin/aion/data/logs/debug.log
```

Look at the `FULL SYSTEM PROMPT` section. Verify:
- SOUL.md content appears at the END of the system prompt (not the beginning)
- Memories (if any) appear before SOUL.md
- Skills appear before SOUL.md
- The single-user guidance appears before SOUL.md

### Step 4: Check the greeting response

The response should reflect SOUL.md personality, not "How can I help you today?"

**Report back:** The greeting response and whether SOUL.md is at the bottom of the debug log's system prompt output.
