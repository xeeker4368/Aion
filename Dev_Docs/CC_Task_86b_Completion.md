# CC Task 86b — Draft / Review / Revise Loop Completion

**Author:** Claude (Session 22)
**Status:** Ready to execute
**Type:** Follow-up to Task 86 — completes work that was partially executed
**Depends on:** Task 86 must already be deployed
**Estimated scope:** ~80 lines across 3 files

---

## Purpose

Task 86 was partially executed. The backend loop function exists and the server wires it up, but four things were missed:

1. The `self_reviews` table was never created in the dev database
2. The `ChatResponse` model in `server.py` was not extended with the new `draft` and `review` fields
3. The chat endpoint return statement was not updated to pass those fields
4. None of the `static/index.html` UI changes were made

This task does only those four things. **Do not re-execute any of Task 86's other changes.** The function `draft_review_revise` in chat.py already exists, the database helper `save_self_review` exists, the `create_self_review_chunk` helper in memory.py exists, and the server's chat handler already calls them. Those parts are correct. This task only adds what was missed.

---

## Context for CC

You do not need to read Task 86 to execute this task. Everything you need is in this file. If you find yourself wanting to also touch the parts of Task 86 that already exist, stop — they are correct as-is and changing them risks breaking what works.

---

## Change 1 — Initialize the dev database

Run this from the project root:

```bash
cd ~/aion
./aion/bin/python -c "import db; db.init_databases()"
sqlite3 data/dev/working.db ".schema self_reviews"
```

The schema output should print and contain six columns: `id`, `message_id`, `conversation_id`, `draft`, `review`, `created_at`. It should also print the two indexes `idx_self_reviews_message_id` and `idx_self_reviews_conversation_id`. If any of these are missing, the table creation in `db.py` is wrong and needs fixing before continuing.

**Do NOT initialize the production database.** Only dev. Lyle will decide when to migrate prod after testing.

---

## Change 2 — Extend the `ChatResponse` model in `server.py`

Find the existing `ChatResponse` model (around line 180):

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

Both new fields default to `None`. Existing clients are unaffected. Responses where the loop did not run will continue to omit these fields entirely from the JSON payload.

---

## Change 3 — Update the chat endpoint return statement

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

The variables `draft_for_storage`, `critique_for_storage`, and `used_review_loop` already exist in the chat handler scope from Task 86. Do not redefine them.

---

## Change 4 — Add CSS for the reasoning disclosure in `static/index.html`

Find the existing `.pill.tool` CSS rule (around line 101). Immediately after it, add these new rules:

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

Do not modify any existing CSS. Only add these rules.

---

## Change 5 — Update `addMessage()` to render reasoning

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

The function gains a fourth parameter `reasoning`. The disclosure is rendered between the message body and the metadata pills. The disclosure is only added if `reasoning` is truthy AND at least one of `reasoning.draft` or `reasoning.review` is non-empty. Both `reasoning.draft` and `reasoning.review` are rendered using `textContent` — never `innerHTML` — so model output cannot inject HTML into the page. This is required.

---

## Change 6 — Update the chat send handler to pass reasoning to `addMessage`

Find this block in the chat send handler (around line 507):

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

This changes the cache shape from `_debugCache[idx] = debug` to `_debugCache[idx] = { debug, reasoning }`. The conversation reload path (Change 7) handles both shapes so existing cached data does not break.

---

## Change 7 — Update the conversation reload path to handle both cache shapes

Find the conversation reload loop (around line 810). It contains a line that looks something like:

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

The legacy cache shape handling prevents existing cached debug data (from before this task) from breaking the UI on reload. Old cache entries still display their debug pills correctly; they just don't have reasoning to show, which is correct because the loop wasn't running when they were saved.

---

## Change 8 — Update the cache key lookup for the "last debug" restore

Find this section in the conversation reload path (around line 815):

```javascript
            if (Object.keys(_debugCache).length) {
                const lastIdx = Math.max(...Object.keys(_debugCache).map(Number));
```

The next line (or one nearby) calls `updateContext(_debugCache[lastIdx])` or similar. Find the call to `updateContext` that uses the cached debug entry and update it to handle both shapes. The replacement logic:

```javascript
                const lastEntry = _debugCache[lastIdx];
                const lastDebugData = (lastEntry && typeof lastEntry === 'object' && 'debug' in lastEntry) ? lastEntry.debug : lastEntry;
                if (lastDebugData) updateContext(lastDebugData);
```

Preserve any existing conditional wrapping around the `updateContext` call — only swap the value being passed in.

---

## What NOT to Do

- **Do NOT modify `chat.py`.** The `draft_review_revise` function and the prompts already exist correctly. Touching them risks breaking the validated v11/v12 prompts.
- **Do NOT modify `db.py` beyond running `init_databases()` once.** The schema and helper functions already exist correctly. Touching them risks corrupting working data.
- **Do NOT modify `memory.py`.** The `create_self_review_chunk` helper and the `self_review` trust weight already exist correctly.
- **Do NOT modify `config.py`.** The flag and temperature constants already exist correctly.
- **Do NOT modify any part of `server.py` other than the `ChatResponse` model and the final `return ChatResponse(...)` statement.** The chat handler logic that calls `draft_review_revise` and saves the self_review is already wired correctly.
- **Do NOT initialize the production database.** Only dev. Lyle decides when to migrate prod.
- **Do NOT enable the feature flag.** Lyle will set `DRAFT_REVIEW_REVISE_ENABLED: true` in `data/dev/config.json` after this task is verified.
- **Do NOT use `innerHTML` for the draft or review text in the UI.** Use `textContent`. Model output is attacker-reachable in principle and rendering it as HTML is a security hole.
- **Do NOT bundle this with anything else.** If you notice other issues in the files you're editing, do not fix them. Flag them separately.

---

## Verification Steps

Perform in order. Stop and report failure if any step fails.

### Step 1 — Schema check

```
sqlite3 data/dev/working.db ".schema self_reviews"
```

Must return the table definition with all six columns and both indexes. If empty, Change 1 failed.

### Step 2 — Python syntax check

```
cd ~/aion
./aion/bin/python -m py_compile server.py
```

Must compile without output. If it fails, the `ChatResponse` edits are wrong.

### Step 3 — Model field check

```
./aion/bin/python -c "from server import ChatResponse; r = ChatResponse(response='x', conversation_id='y', memories_used=0); print(r.draft, r.review)"
```

Must print `None None`. If `draft` or `review` raise an AttributeError, Change 2 failed.

### Step 4 — UI smoke check (no feature flag yet)

Start the dev server:

```
./start.sh   # pick dev
```

Open the UI in a browser. Open browser devtools console. The console should be empty — no JavaScript errors. Send a normal chat message. The response should arrive normally with no reasoning disclosure (because the flag is still off and `draft`/`review` are `None`).

If there are console errors, the `static/index.html` edits are broken. Fix before proceeding.

### Step 5 — Feature flag on, end-to-end check

Stop the dev server. Edit `data/dev/config.json` to add (creating the file if it doesn't exist):

```json
{
  "DRAFT_REVIEW_REVISE_ENABLED": true
}
```

Restart dev. Send a substantive chat message (not a greeting). Verify:

- Response takes noticeably longer than usual (3x — this is expected)
- A small "reasoning" link appears below the assistant's message
- Click it. It expands to show two blocks: "first draft" and "looking at it"
- Both blocks contain text that is plainly different from the final response
- The "first draft" text matches what you can read from `sqlite3 data/dev/working.db "SELECT draft FROM self_reviews ORDER BY created_at DESC LIMIT 1"`
- The "looking at it" text matches `sqlite3 data/dev/working.db "SELECT review FROM self_reviews ORDER BY created_at DESC LIMIT 1"`
- Send "hey" — the response should be fast and have NO reasoning disclosure (trivial messages skip the loop)
- Reload the browser — the previous message's reasoning disclosure should still be present (cache persistence works)

### Step 6 — Production untouched check

```
sqlite3 data/prod/working.db ".schema self_reviews"
```

Must return empty. Production has not been touched. If this returns the table definition, Change 1 was wrongly applied to prod.

---

## Completion Notes (to fill in when done)

1. Confirmation that all six verification steps passed
2. The first few words of the actual `first draft` and `looking at it` text from a real chat message (confirms the data flowing end to end)
3. Any browser console warnings or errors during testing
4. Anything you noticed that needed fixing but did not touch

---

*CC Task 86b · Draft/Review/Revise Loop Completion · Session 22*
