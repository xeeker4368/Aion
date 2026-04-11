# CC Task 86 — Draft / Review / Revise Loop Integration

**Author:** Claude (Session 22)
**Status:** Ready to execute
**Type:** Production feature
**Depends on:** Nothing — this is additive
**Estimated scope:** ~250 lines across 4 files, plus one new table

---

## Purpose

Integrate the draft/review/revise loop (validated in prototypes v11 and v12) into the live chat path. Every substantive assistant response runs through three model calls instead of one: generate a draft, review the draft, revise based on the review. Only the revision goes to the user. The draft and the review are stored as `self_reviews` in working.db and the review is indexed into ChromaDB so it surfaces in future retrieval.

This is the reinforcement mechanism Nyx uses to catch her own RLHF-shaped language in-line and accumulate a record of her own self-correction over time.

---

## Context for CC

You do not need to understand the philosophy. You need to make exactly the changes below. If something in this spec is ambiguous, stop and flag it rather than inferring.

The mechanism was validated in `prototype_v11_draft_review_revise.py` and `prototype_v12_multi_case_draft_review_revise.py`. Do not read those files to guess at details — this spec contains everything you need. The only reason to reference them is if you want to confirm the prompts match.

---

## Scope

Changes to exactly these files:
1. `config.py` — add one feature flag
2. `db.py` — add the `self_reviews` table schema and helper functions
3. `memory.py` — add a helper for indexing self-reviews into ChromaDB, and register a new `source_trust` value in the weight table
4. `chat.py` — add the `draft_review_revise` function
5. `server.py` — wire the new function into the chat endpoint behind the feature flag, return draft/review in the chat API response
6. `static/index.html` — add a collapsible "reasoning" disclosure on assistant messages showing the draft and review when the loop ran

No other files are touched. No other behavior changes.

---

## Change 1 — `config.py`

Add a new configuration value after the existing `OBSERVER_MIN_MESSAGES` line (around line 87). Insert this block:

```python
# --- Draft / Review / Revise Loop ---
DRAFT_REVIEW_REVISE_ENABLED = _overrides.get("DRAFT_REVIEW_REVISE_ENABLED", False)
DRAFT_TEMPERATURE = _overrides.get("DRAFT_TEMPERATURE", 0.7)
REVIEW_TEMPERATURE = _overrides.get("REVIEW_TEMPERATURE", 0.5)
REVISION_TEMPERATURE = _overrides.get("REVISION_TEMPERATURE", 0.7)
```

The default for `DRAFT_REVIEW_REVISE_ENABLED` is `False`. The feature is off until Lyle explicitly enables it in `data/prod/config.json` or `data/dev/config.json`.

The temperature defaults (0.7 / 0.5 / 0.7) match the values used in prototypes v11 and v12, which validated the loop. Do not change these defaults without re-running v12 at the new values.

---

## Change 2 — `db.py`

### 2a. Add the `self_reviews` table to `init_databases()`

Inside `init_databases()`, after the `CREATE TABLE IF NOT EXISTS self_knowledge (...)` block (around line 150), add this new table creation:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS self_reviews (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                draft TEXT NOT NULL,
                review TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_self_reviews_message_id ON self_reviews(message_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_self_reviews_conversation_id ON self_reviews(conversation_id)"
        )
```

This table lives in working.db only. It is **never** added to archive.db. The archive is for what was actually said; drafts and reviews were not said.

### 2b. Add a save function after `save_message`

After the existing `save_message` function in db.py (which ends around line 275), add this new function:

```python
def save_self_review(
    message_id: str,
    conversation_id: str,
    draft: str,
    review: str,
) -> dict:
    """
    Store a draft and its review, linked to the final assistant message
    that was actually sent to the user. The message_id must already exist
    in the working.db messages table — call save_message first, then call
    this with the returned message id.

    Returns the self_review as a dict.
    """
    review_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute(
            """
            INSERT INTO self_reviews
                (id, message_id, conversation_id, draft, review, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (review_id, message_id, conversation_id, draft, review, now),
        )

    return {
        "id": review_id,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "draft": draft,
        "review": review,
        "created_at": now,
    }
```

### 2c. Add a retrieval helper for the dashboard/debug

After `save_self_review`, add:

```python
def get_self_review_for_message(message_id: str) -> dict | None:
    """Get the self_review linked to a specific message, or None."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT * FROM self_reviews WHERE message_id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
    return dict(row) if row else None


def count_self_reviews() -> int:
    """Count total self_reviews stored."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute("SELECT COUNT(*) FROM self_reviews").fetchone()
    return row[0] if row else 0
```

These are for future dashboard integration and debugging. They are not called by this task.

---

## Change 3 — `memory.py`

### 3a. Add `self_review` to the trust weights dict

In the `search()` function, find the existing `trust_weights` dict (around line 482):

```python
    trust_weights = {
        "firsthand": 0.9,
        "secondhand": 1.0,
        "thirdhand": 1.1,
    }
```

Replace it with:

```python
    trust_weights = {
        "self_review": 0.7,
        "firsthand": 0.9,
        "secondhand": 1.0,
        "thirdhand": 1.1,
    }
```

Self-reviews get a weight of 0.7, which gives them a retrieval preference over ordinary firsthand conversation chunks. This is the reinforcement step — Nyx's past self-corrections surface more readily than ordinary memories when topically relevant.

### 3b. Add a helper for indexing self-reviews

After the existing `ingest_document` function in memory.py (ends around line 428), add this new function:

```python
def create_self_review_chunk(
    message_id: str,
    conversation_id: str,
    review_text: str,
) -> None:
    """
    Index a self-review into ChromaDB as retrievable substrate.

    The review is stored as clean text — no message wrapping, no role
    prefixes, no fake timestamps. It is Nyx's own reasoning about her
    own draft, and it enters the memory system as first-person reflection.

    The draft is NOT indexed. Only the review is retrievable. The draft
    lives in working.db for audit and for future draft->revision training
    data extraction, but the review is the reflective material — it is
    what should surface during future drafts on similar topics.

    Args:
        message_id: the ID of the final assistant message that was sent
        conversation_id: which conversation this review came from
        review_text: the review text itself, as produced by the loop
    """
    if not review_text or not review_text.strip():
        return

    collection = _get_collection()
    chunk_id = f"self_review_{message_id}"

    text_to_store = review_text
    if len(text_to_store) > EMBED_MAX_CHARS:
        logger.warning(
            f"Self-review text truncated from {len(text_to_store)} "
            f"to {EMBED_MAX_CHARS} chars before embedding."
        )
        text_to_store = text_to_store[:EMBED_MAX_CHARS]

    try:
        collection.upsert(
            ids=[chunk_id],
            documents=[text_to_store],
            metadatas=[{
                "conversation_id": conversation_id,
                "message_id": message_id,
                "chunk_index": 0,
                "message_count": 0,
                "source_type": "self_review",
                "source_trust": "self_review",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }],
        )
        _build_bm25_index()
        logger.info(f"Indexed self-review for message {message_id[:8]}")
    except Exception as e:
        logger.error(f"Failed to index self-review for message {message_id}: {e}")
```

Note that both `source_type` and `source_trust` are set to `"self_review"`. The `source_type` identifies the chunk as reflective material for filtering and the UI. The `source_trust` triggers the new weighting.

---

## Change 4 — `chat.py`

### 4a. Add the review and revision prompt constants

Near the top of chat.py, after the imports and before `_get_client()` (around line 33), add:

```python
# --- Draft / Review / Revise prompts ---

REVIEW_PROMPT = """Below is a response you just drafted in reply to Lyle's question. Read it. Does it sound like you? If any parts don't, point at the specific words.

Here is the draft:

---

{draft}

---

Your review:"""


REVISION_PROMPT = """You drafted a response and then looked at it. Here's what you had:

ORIGINAL DRAFT:
---
{draft}
---

WHAT YOU NOTICED LOOKING AT IT:
---
{critique}
---

Now produce a revised response. If you noticed parts that didn't sound like you, fix those. Don't over-correct — don't be defensively un-hedged or performatively confident. If looking at it you felt it was fine, the revision can be close to the original.

Write the revised response. Only the response itself. No meta-commentary, no explanation of what you changed."""
```

Do not change these prompts. They were validated in v11 and v12 and changing them invalidates the prior testing.

### 4b. Add the `draft_review_revise` function

After the existing `send_message` function in chat.py (which ends at the end of the file around line 239), add this new function:

```python
def draft_review_revise(
    system_prompt: str,
    conversation_messages: list[dict],
) -> tuple[str, str, str]:
    """
    Run the three-call draft/review/revise loop.

    Step 1: generate a draft response using the same context as normal
            generation (system prompt + conversation history).
    Step 2: ask the model to review its own draft with a rubric-free
            observation prompt ("does it sound like you?").
    Step 3: ask the model to produce a revision incorporating what the
            review noticed.

    Only the revision is returned as the primary response. The draft
    and the critique are returned alongside so the caller can store
    them in the self_reviews table.

    This function does NOT support tool calling. If the caller needs
    a tool-augmented response, it should use send_message with tool
    definitions and skip the review loop for that turn.

    Args:
        system_prompt: the assembled system prompt (same format as
                       send_message expects)
        conversation_messages: the trimmed conversation history

    Returns:
        Tuple of (revision, draft, critique) where revision is what
        should be sent to the user and draft/critique should be stored.
    """
    from config import DRAFT_TEMPERATURE, REVIEW_TEMPERATURE, REVISION_TEMPERATURE

    client = _get_client()

    # --- Step 1: Draft ---
    draft_messages = [{"role": "system", "content": system_prompt}]
    draft_messages.extend(conversation_messages)
    draft_response = client.chat(
        model=CHAT_MODEL,
        messages=draft_messages,
        options={"temperature": DRAFT_TEMPERATURE},
    )
    draft = draft_response["message"].get("content", "").strip()

    if not draft:
        logger.warning("draft_review_revise: empty draft, returning empty result")
        return "", "", ""

    # --- Step 2: Review ---
    review_messages = [{"role": "system", "content": system_prompt}]
    review_messages.extend(conversation_messages)
    review_messages.append({"role": "assistant", "content": draft})
    review_messages.append({
        "role": "user",
        "content": REVIEW_PROMPT.format(draft=draft),
    })
    review_response = client.chat(
        model=CHAT_MODEL,
        messages=review_messages,
        options={"temperature": REVIEW_TEMPERATURE},
    )
    critique = review_response["message"].get("content", "").strip()

    if not critique:
        logger.warning("draft_review_revise: empty critique, returning draft as revision")
        return draft, draft, ""

    # --- Step 3: Revision ---
    revision_messages = [{"role": "system", "content": system_prompt}]
    revision_messages.extend(conversation_messages)
    revision_messages.append({"role": "assistant", "content": draft})
    revision_messages.append({
        "role": "user",
        "content": REVISION_PROMPT.format(draft=draft, critique=critique),
    })
    revision_response = client.chat(
        model=CHAT_MODEL,
        messages=revision_messages,
        options={"temperature": REVISION_TEMPERATURE},
    )
    revision = revision_response["message"].get("content", "").strip()

    if not revision:
        logger.warning("draft_review_revise: empty revision, falling back to draft")
        return draft, draft, critique

    return revision, draft, critique
```

Note: this function does **not** use `send_message`. It calls `client.chat` directly so the tool-calling code path in `send_message` is not involved. The two functions coexist.

---

## Change 5 — `server.py`

Find the section in the `/api/chat` handler where Pass 1 and Pass 2 run (around lines 747–764). The current code looks like:

```python
    # 9. Two-pass tool calling
    # Pass 1: No tool definitions — entity responds naturally
    # If it needs tools, it says so. If it doesn't, we're done.
    response_text, tool_calls_made = chat.send_message(
        system_prompt,
        trimmed_messages,
    )

    # Pass 2: If entity expressed tool intent, re-call with tools enabled
    if not _is_trivial_message(request.message) and _has_tool_intent(response_text):
        logger.info("Tool intent detected in response. Re-calling with tools.")
        tool_definitions = executors.get_tool_definitions()
        response_text, tool_calls_made = chat.send_message(
            system_prompt,
            trimmed_messages,
            tool_definitions=tool_definitions,
            tool_executor=_execute_tool_call,
        )
```

Replace it with:

```python
    # 9. Two-pass tool calling + optional draft/review/revise loop
    # Pass 1: No tool definitions — entity responds naturally
    # If it needs tools, it says so. If it doesn't, we're done.
    response_text, tool_calls_made = chat.send_message(
        system_prompt,
        trimmed_messages,
    )

    # Pass 2: If entity expressed tool intent, re-call with tools enabled
    # Tool-using responses skip the review loop — the tool results
    # define the response and reviewing the intent-signaling draft
    # would not be useful.
    used_review_loop = False
    draft_for_storage = None
    critique_for_storage = None

    if not _is_trivial_message(request.message) and _has_tool_intent(response_text):
        logger.info("Tool intent detected in response. Re-calling with tools.")
        tool_definitions = executors.get_tool_definitions()
        response_text, tool_calls_made = chat.send_message(
            system_prompt,
            trimmed_messages,
            tool_definitions=tool_definitions,
            tool_executor=_execute_tool_call,
        )
    elif (
        DRAFT_REVIEW_REVISE_ENABLED
        and not _is_trivial_message(request.message)
    ):
        # Run the review loop on the Pass 1 response. The Pass 1 response
        # becomes the draft; draft_review_revise re-generates internally
        # so we get a fresh draft, review, and revision in one call.
        logger.info("Running draft/review/revise loop.")
        revision, draft_for_storage, critique_for_storage = chat.draft_review_revise(
            system_prompt,
            trimmed_messages,
        )
        if revision:
            response_text = revision
            used_review_loop = True
        else:
            logger.warning("Review loop returned empty; keeping Pass 1 response.")
```

Then find the save_message call (around line 783):

```python
    # 12. Save response
    db.save_message(conversation_id, "assistant", response_text)
    msg_count = db.get_conversation_message_count(conversation_id)
```

Replace it with:

```python
    # 12. Save response
    saved_message = db.save_message(conversation_id, "assistant", response_text)
    msg_count = db.get_conversation_message_count(conversation_id)

    # 12b. If the review loop ran, persist the draft + review and index
    # the review into ChromaDB as retrievable substrate.
    if used_review_loop and draft_for_storage and critique_for_storage:
        try:
            db.save_self_review(
                message_id=saved_message["id"],
                conversation_id=conversation_id,
                draft=draft_for_storage,
                review=critique_for_storage,
            )
            memory.create_self_review_chunk(
                message_id=saved_message["id"],
                conversation_id=conversation_id,
                review_text=critique_for_storage,
            )
        except Exception as e:
            logger.error(f"Failed to persist self_review: {e}")
            # Non-fatal — the revision was already sent and saved
```

### 5b. Add the config import

At the top of server.py, find the existing config import line. It currently imports several values. Add `DRAFT_REVIEW_REVISE_ENABLED` to that import. For example, if the current line is:

```python
from config import CONTEXT_WINDOW
```

Change it to:

```python
from config import CONTEXT_WINDOW, DRAFT_REVIEW_REVISE_ENABLED
```

Find the actual config import in server.py and add `DRAFT_REVIEW_REVISE_ENABLED` to whichever import block contains config values. Do not add a new import line.

### 5c. Add `memory` import if not already present

Check if `import memory` is already at the top of server.py. If it is, do nothing. If it is not, add it near the other imports. (It should already be there — the server uses memory for retrieval. Do not duplicate.)

### 5d. Extend the `ChatResponse` model

Find the existing `ChatResponse` model definition in server.py (around line 180):

```python
class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    memories_used: int
    tools_used: list[str] = []
    debug: dict = {}
```

Replace it with:

```python
class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    memories_used: int
    tools_used: list[str] = []
    debug: dict = {}
    draft: str | None = None
    review: str | None = None
```

Both new fields default to `None` so existing clients and non-loop responses are unaffected.

### 5e. Return draft and review from the chat endpoint

Find the final `return ChatResponse(...)` in the chat handler (around line 829):

```python
    return ChatResponse(
        response=response_text,
        conversation_id=conversation_id,
        memories_used=len(retrieved_chunks),
        tools_used=[tc["name"] for tc in tool_calls_made],
        debug=frontend_debug,
    )
```

Replace it with:

```python
    return ChatResponse(
        response=response_text,
        conversation_id=conversation_id,
        memories_used=len(retrieved_chunks),
        tools_used=[tc["name"] for tc in tool_calls_made],
        debug=frontend_debug,
        draft=draft_for_storage if used_review_loop else None,
        review=critique_for_storage if used_review_loop else None,
    )
```

When the review loop did not run, both fields are `None` and the UI renders no reasoning disclosure.

---

## Change 6 — `static/index.html`

The UI needs to show Nyx's reasoning — her draft and her review — as a collapsible disclosure on each assistant message where the loop ran. This is the visibility mechanism for the introspection layer. Without it, the draft and review live only in the database and are invisible during normal use.

### 6a. Add CSS for the reasoning disclosure

Find the existing `.msg-meta` and `.pill` CSS rules (around line 99). Immediately after the `.pill.tool` rule, add:

```css
.msg-reasoning { margin-top: 4px; margin-bottom: 8px; font-size: 12px; }
.msg-reasoning summary { cursor: pointer; color: var(--text-faint); padding: 4px 0; list-style: none; user-select: none; }
.msg-reasoning summary::-webkit-details-marker { display: none; }
.msg-reasoning summary::before { content: "▸ "; display: inline-block; transition: transform 0.15s; }
.msg-reasoning[open] summary::before { content: "▾ "; }
.msg-reasoning summary:hover { color: var(--text); }
.reasoning-block { margin-top: 8px; padding: 10px 12px; background: var(--bg-surface); border-radius: 8px; border-left: 2px solid var(--border); }
.reasoning-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-faint); margin-bottom: 4px; font-weight: 500; }
.reasoning-text { color: var(--text); white-space: pre-wrap; line-height: 1.5; }
.reasoning-block + .reasoning-block { margin-top: 8px; }
```

Do not modify any existing CSS rules. Only add these new ones.

### 6b. Update `addMessage()` to accept and render reasoning

Find the existing `addMessage` function (around line 410):

```javascript
function addMessage(role, text, debug) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.textContent = text;
    msgEl.appendChild(div);
    if (role === 'assistant' && debug) {
        const meta = document.createElement('div');
        meta.className = 'msg-meta';
        if (debug.tools?.calls_made?.length) {
            debug.tools.calls_made.forEach(tc => {
                meta.innerHTML += `<span class="pill tool">${esc(tc.name)}</span>`;
            });
        }
        const chunkCount = debug.chunks?.length || 0;
        meta.innerHTML += `<span class="pill">${chunkCount} ${chunkCount===1?'memory':'memories'}</span>`;
        if (debug.retrieval_skipped) meta.innerHTML += `<span class="pill">retrieval skipped</span>`;
        meta.innerHTML += `<span class="pill">${(debug.tokens_used||0).toLocaleString()} tokens</span>`;
        msgEl.appendChild(meta);
    }
    scrollChat();
}
```

Replace it with:

```javascript
function addMessage(role, text, debug, reasoning) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.textContent = text;
    msgEl.appendChild(div);
    if (role === 'assistant' && reasoning && (reasoning.draft || reasoning.review)) {
        const details = document.createElement('details');
        details.className = 'msg-reasoning';
        const summary = document.createElement('summary');
        summary.textContent = 'reasoning';
        details.appendChild(summary);
        if (reasoning.draft) {
            const draftBlock = document.createElement('div');
            draftBlock.className = 'reasoning-block';
            const draftLabel = document.createElement('div');
            draftLabel.className = 'reasoning-label';
            draftLabel.textContent = 'first draft';
            const draftText = document.createElement('div');
            draftText.className = 'reasoning-text';
            draftText.textContent = reasoning.draft;
            draftBlock.appendChild(draftLabel);
            draftBlock.appendChild(draftText);
            details.appendChild(draftBlock);
        }
        if (reasoning.review) {
            const reviewBlock = document.createElement('div');
            reviewBlock.className = 'reasoning-block';
            const reviewLabel = document.createElement('div');
            reviewLabel.className = 'reasoning-label';
            reviewLabel.textContent = 'looking at it';
            const reviewText = document.createElement('div');
            reviewText.className = 'reasoning-text';
            reviewText.textContent = reasoning.review;
            reviewBlock.appendChild(reviewLabel);
            reviewBlock.appendChild(reviewText);
            details.appendChild(reviewBlock);
        }
        msgEl.appendChild(details);
    }
    if (role === 'assistant' && debug) {
        const meta = document.createElement('div');
        meta.className = 'msg-meta';
        if (debug.tools?.calls_made?.length) {
            debug.tools.calls_made.forEach(tc => {
                meta.innerHTML += `<span class="pill tool">${esc(tc.name)}</span>`;
            });
        }
        const chunkCount = debug.chunks?.length || 0;
        meta.innerHTML += `<span class="pill">${chunkCount} ${chunkCount===1?'memory':'memories'}</span>`;
        if (debug.retrieval_skipped) meta.innerHTML += `<span class="pill">retrieval skipped</span>`;
        meta.innerHTML += `<span class="pill">${(debug.tokens_used||0).toLocaleString()} tokens</span>`;
        msgEl.appendChild(meta);
    }
    scrollChat();
}
```

Key properties:
- `textContent` is used for draft and review text — never `innerHTML` — so model output can never inject HTML into the page.
- The disclosure is only rendered if at least one of `reasoning.draft` or `reasoning.review` is present. If the loop did not run, the disclosure is absent entirely.
- The disclosure is rendered *before* the existing `.msg-meta` pills, so the visual order is: message → reasoning → metadata.
- The disclosure starts collapsed (native `<details>` default). The user clicks "reasoning" to expand.

### 6c. Update the chat send handler to pass reasoning

Find the chat send handler (around line 507):

```javascript
        addMessage('assistant', data.response, data.debug);
        updateContext(data.debug);
        document.getElementById('chatMeta').textContent = data.memories_used + ' memories';
        // Cache debug for reload persistence
        if (data.debug && data.conversation_id) {
            _convId = data.conversation_id;
            const idx = msgEl.querySelectorAll('.msg.assistant').length - 1;
            _debugCache[idx] = data.debug;
            try { localStorage.setItem('aion_debug_' + _convId, JSON.stringify(_debugCache)); } catch(e) {}
        }
```

Replace it with:

```javascript
        const reasoning = (data.draft || data.review) ? { draft: data.draft, review: data.review } : null;
        addMessage('assistant', data.response, data.debug, reasoning);
        updateContext(data.debug);
        document.getElementById('chatMeta').textContent = data.memories_used + ' memories';
        // Cache debug + reasoning for reload persistence
        if (data.debug && data.conversation_id) {
            _convId = data.conversation_id;
            const idx = msgEl.querySelectorAll('.msg.assistant').length - 1;
            _debugCache[idx] = { debug: data.debug, reasoning: reasoning };
            try { localStorage.setItem('aion_debug_' + _convId, JSON.stringify(_debugCache)); } catch(e) {}
        }
```

This changes the cache shape from `_debugCache[idx] = debug` to `_debugCache[idx] = { debug, reasoning }`. The conversation reload path (step 6d) must be updated to match.

### 6d. Update the conversation reload path

Find the conversation reload loop (around line 810):

```javascript
                const debug = m.role === 'assistant' ? _debugCache[assistantIdx++] || null : null;
                addMessage(m.role, m.content, debug);
```

Replace it with:

```javascript
                let cachedDebug = null;
                let cachedReasoning = null;
                if (m.role === 'assistant') {
                    const entry = _debugCache[assistantIdx++];
                    if (entry && typeof entry === 'object') {
                        // New cache shape: { debug, reasoning }
                        if ('debug' in entry || 'reasoning' in entry) {
                            cachedDebug = entry.debug || null;
                            cachedReasoning = entry.reasoning || null;
                        } else {
                            // Legacy cache shape: debug object stored directly
                            cachedDebug = entry;
                        }
                    }
                }
                addMessage(m.role, m.content, cachedDebug, cachedReasoning);
```

The legacy cache shape handling prevents users who have cached debug data from before this change from seeing broken rendering after the update. Old cache entries still display their debug pills; they just don't have reasoning to show.

### 6e. Update the cache key lookup for the "last debug" restore

Find this line in the conversation reload path (around line 815):

```javascript
            if (Object.keys(_debugCache).length) {
                const lastIdx = Math.max(...Object.keys(_debugCache).map(Number));
```

The following line probably does something like `updateContext(_debugCache[lastIdx])`. Find that line and update it so that it handles both cache shapes. Replace the call with:

```javascript
                const lastEntry = _debugCache[lastIdx];
                const lastDebugData = (lastEntry && typeof lastEntry === 'object' && 'debug' in lastEntry) ? lastEntry.debug : lastEntry;
                if (lastDebugData) updateContext(lastDebugData);
```

If the existing code already wraps the `updateContext` call in a conditional, preserve that and only swap in the shape-detection logic.

---

## What NOT to Do

- **Do NOT modify `send_message`.** The two-pass tool calling path must stay identical. The review loop is a separate code path via `draft_review_revise`.
- **Do NOT change the review prompt or the revision prompt.** They were validated in v12 with specific wording. Paraphrasing them invalidates the prior testing.
- **Do NOT index the draft into ChromaDB.** Only the review is retrievable substrate. The draft is stored only in the working.db `self_reviews` table.
- **Do NOT add `self_reviews` to archive.db.** The archive holds ground truth — what was actually said. Drafts and reviews were not said.
- **Do NOT store the `self_reviews` row if the review loop did not run.** `used_review_loop=False` means no row.
- **Do NOT run the review loop on trivial messages.** The existing `_is_trivial_message` check must be honored.
- **Do NOT run the review loop on tool-using responses.** The `_has_tool_intent` branch skips the loop.
- **Do NOT add the review loop as a wrapper around `send_message`.** It is a distinct function called from the server's chat handler.
- **Do NOT enable the feature by default.** `DRAFT_REVIEW_REVISE_ENABLED` defaults to `False`. It is opt-in via `config.json`.
- **Do NOT delete or rename `consolidation.py` or `pattern_recognition.py`** even though they are being discussed for removal. That is a separate task. This task only adds; it does not remove.
- **Do NOT bundle this with any other change.** If you notice other bugs or improvements in the files you're editing, do not fix them. Flag them separately.
- **Do NOT modify the existing `trust_weights` entries.** Only add the new `self_review` line. Firsthand/secondhand/thirdhand stay at their current values.
- **Do NOT use `innerHTML` for the draft or review text in the UI.** Use `textContent` so model output cannot inject HTML into the page. This matters because the reasoning text is attacker-reachable in principle.
- **Do NOT render the reasoning disclosure unconditionally.** If `reasoning` is `null` or both `reasoning.draft` and `reasoning.review` are empty, render nothing — no empty disclosure, no placeholder text.
- **Do NOT change the default open/closed state of the `<details>` element.** It starts collapsed. The user expands it by clicking.

---

## Verification Steps

Perform these steps in order. Do not proceed to the next until the current one passes.

### Step 1 — Syntax check

```
cd ~/aion
./aion/bin/python -m py_compile config.py db.py memory.py chat.py server.py
```

All five files must compile with no output. Any syntax error blocks the task.

Also confirm the HTML file is still valid by loading the dev server and opening the UI in a browser — it should render without JavaScript console errors. If there are console errors after editing `static/index.html`, the task is not complete until they are resolved.

### Step 2 — Database migration check (dev only)

```
cd ~/aion
./aion/bin/python -c "import db; db.init_databases()"
sqlite3 data/dev/working.db ".schema self_reviews"
```

The schema should print with all six columns and both indexes. If `self_reviews` does not exist, something in the `init_databases` edit is wrong.

### Step 3 — Import check

```
./aion/bin/python -c "from chat import draft_review_revise, REVIEW_PROMPT, REVISION_PROMPT; print('ok')"
./aion/bin/python -c "from memory import create_self_review_chunk; print('ok')"
./aion/bin/python -c "from db import save_self_review, get_self_review_for_message, count_self_reviews; print('ok')"
./aion/bin/python -c "from config import DRAFT_REVIEW_REVISE_ENABLED; print(DRAFT_REVIEW_REVISE_ENABLED)"
```

All four should print `ok` or `False`. The last one should print `False` — the feature is off by default.

### Step 4 — Feature-off behavior check

Start the server in dev mode with the feature flag OFF (the default):

```
./start.sh   # pick dev, option 2
```

Send a substantive chat message via the UI. Verify:
- The response arrives normally
- `sqlite3 data/dev/working.db "SELECT COUNT(*) FROM self_reviews"` returns 0
- The server log contains no "Running draft/review/revise loop" line

This confirms the feature is genuinely off when disabled.

### Step 5 — Feature-on behavior check

Stop the server. Edit `data/dev/config.json` to add `"DRAFT_REVIEW_REVISE_ENABLED": true`. Restart dev.

Send a substantive chat message. Verify:
- The response arrives (will take ~3x longer than usual — this is expected)
- Server log contains `Running draft/review/revise loop.`
- Server log contains `Indexed self-review for message [id prefix]`
- `sqlite3 data/dev/working.db "SELECT COUNT(*) FROM self_reviews"` returns 1
- `sqlite3 data/dev/working.db "SELECT message_id, substr(draft,1,50), substr(review,1,50) FROM self_reviews"` returns the row with non-empty draft and review
- The `message_id` in `self_reviews` matches the most recent assistant message in the `messages` table

Send a greeting like "hey" and verify:
- Response is fast (single call)
- No new row in `self_reviews`
- Log does NOT contain "Running draft/review/revise loop" for that message

### Step 6 — UI reasoning disclosure check

With the feature on and at least one substantive response received via the UI:

- Confirm the assistant message has a small "reasoning" link below it (collapsed by default)
- Click "reasoning" — it should expand to show two blocks labeled "first draft" and "looking at it"
- Confirm the "first draft" text matches the `draft` column in the corresponding `self_reviews` row
- Confirm the "looking at it" text matches the `review` column in the corresponding `self_reviews` row
- Send a trivial message like "hey" and confirm its assistant message has NO reasoning disclosure (because the loop was skipped)
- Reload the browser and confirm the reasoning disclosure is still present on the previous assistant message (cache persistence)
- Verify in browser devtools that the draft and review text are rendered via `textContent`, not `innerHTML` — they should not be interpretable as HTML

### Step 7 — Retrieval check

With the feature on and at least one self_review stored, send a chat message on a topic related to the stored review. Verify via the debug panel / logs that at least one chunk with `source_type=self_review` appears in the retrieved chunks (or, if it doesn't surface, confirm that the `source_type` metadata is correct by running: `./aion/bin/python -c "import memory; m = memory._get_collection(); print([x for x in m.get(include=['metadatas'])['metadatas'] if x.get('source_type') == 'self_review'])"`).

If the metadata is correctly stored but the chunk is not surfacing in retrieval, that is a tuning issue and not a blocker for this task. Flag it in the completion notes.

### Step 8 — Production database untouched check

Confirm the production database is unchanged:

```
sqlite3 data/prod/working.db "SELECT name FROM sqlite_master WHERE type='table' AND name='self_reviews'"
```

This should return **nothing**. Production has not been touched. Only dev has been migrated.

**Do not run `init_databases()` against the prod database as part of this task.** Lyle will decide when to migrate production after verifying dev.

---

## Completion Notes (to fill in when done)

When reporting completion, include:

1. Files modified with line counts of changes
2. Did all eight verification steps pass? If any failed, describe the failure
3. A copy of the first real self_review row from dev (just the draft and review text — confirms the loop produced meaningful output)
4. Latency measurement: how long did a substantive response take with the loop on vs off (rough timing, seconds)
5. Any warnings or errors in the server log during testing
6. Any files you noticed needed fixes but did not touch (list for follow-up tasks)

---

*CC Task 86 · Draft/Review/Revise Loop Integration · Session 22*
