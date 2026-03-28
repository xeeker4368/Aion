# CC Task 19: Dead Code Removal and Cleanup

## Overview

After 8 sessions of architecture changes, the codebase has accumulated dead code from abandoned approaches. This task removes it all in one pass. No behavior changes — just deletion and cleanup.

---

## Dead Code to Remove

### 1. `memory.py` — `_remove_live_chunks()`

**Delete the entire function** (lines will vary). It references `is_live` metadata that was removed in Session 7. It's never called anywhere.

```python
# DELETE THIS ENTIRE FUNCTION
def _remove_live_chunks(conversation_id: str):
    """Remove all live chunks for a conversation."""
    ...
```

Verify: `grep -rn "_remove_live_chunks\|is_live" *.py` should return zero results after deletion.

### 2. `executors.py` — `get_tool_definitions()`

**Delete the entire function.** Tools are never passed to the 8b model. Server-side execution is the permanent approach. This function is never called.

```python
# DELETE THIS ENTIRE FUNCTION
def get_tool_definitions() -> list[dict]:
    """
    Get all executors as Ollama tool definitions.
    These get passed to the model so it can call them.
    """
    ...
```

Verify: `grep -rn "get_tool_definitions" *.py` should return zero results after deletion.

### 3. `config.py` — Unused chunking config

**Delete these two lines.** They are from the old final-chunking approach. Current chunking uses `LIVE_CHUNK_INTERVAL` for conversation chunks and `INGEST_CHUNK_SIZE`/`INGEST_CHUNK_OVERLAP` for document ingestion.

```python
# DELETE THESE LINES
CHUNK_SIZE = 10       # messages per chunk
CHUNK_OVERLAP = 5     # overlap between final chunks
```

Also **delete the comment above them** if it says "Final chunks: created when conversation ends" — final chunks use `LIVE_CHUNK_INTERVAL`, not these values.

Verify: `grep -rn "CHUNK_SIZE\|CHUNK_OVERLAP" *.py` — should only return `INGEST_CHUNK_SIZE` and `INGEST_CHUNK_OVERLAP` references.

### 4. `server.py` — Dormant confirmation flow cleanup

The `_pending_search_topic` variable and confirmation handling are effectively dormant. The topic gets set nowhere (the `else` branch that set it was removed in Session 7). The confirmation signals in `_should_offer_tools()` check `_pending_search_topic` which is always `None`.

**Do NOT remove the confirmation infrastructure entirely** — it may be reactivated when the entity-proposes-search flow is built. But **clean up the dead variable state:**

Find and remove:

```python
# At module level, this line stays:
_pending_search_topic: str | None = None

# In handle_chat(), keep the confirmation handling but ensure _pending_search_topic = None 
# is set after every search execution (it already is — just verify)
```

No code changes needed here — just verify the flow is clean. The confirmation signals exist in `_should_offer_tools()` and that's fine — they'll only fire if `_pending_search_topic` is set, which never happens currently.

### 5. `chat (1).py` — Delete stale file

If `chat (1).py` still exists in the project directory, **delete it**. It was the pre-Session-8 corrected version. `chat.py` is now the canonical file.

Verify: `ls -la "chat (1).py"` should return "No such file."

### 6. `executors.py` — Stale docstring

The module docstring says:

```
Executors are registered as Ollama tool functions so the model
can invoke them during conversation. The application handles
the actual execution.
```

**Replace with:**

```
Executors are the entity's built-in capabilities. They are called
server-side — the model never sees tool definitions. The server
detects intent, calls the appropriate executor, and injects results
into the system prompt.
```

### 7. `server.py` — Stale docstring

The module docstring says:

```
6. Sent to Ollama with tool definitions
7. If model calls a tool, execute it and loop back
```

**Replace lines 6-7 with:**

```
6. Sent to Ollama (no tool definitions — server handles tools)
7. Response saved to both databases
```

And renumber the remaining steps (8-10 become 7-9).

### 8. `executors.py` — Tool parameter schemas

Each executor registration includes a full JSON schema for parameters. These were needed when tools were passed to the model. Now they're only used for `execute()` dispatch, which doesn't validate schemas — it just passes kwargs.

**Do NOT remove the schemas.** They serve as documentation and will be useful if a larger model ever uses tool calling directly. But **add a comment:**

```python
def init_executors():
    """Register all built-in executors.
    
    Parameter schemas are retained for documentation and potential
    future use with tool-calling models. Currently, executors are
    called server-side — the model never sees these definitions.
    """
```

---

## Files to Check for Unused Imports

Run `grep -n "^import\|^from" *.py` and verify each import is used. Known candidates:

- `server.py`: `UploadFile` from fastapi — check if any endpoint uses it. If not, remove.
- `executors.py`: `quote_plus` from urllib.parse — check if used. If not, remove.
- `memory.py`: Check all imports are used after `_remove_live_chunks` deletion.

---

## What NOT to Do

- Do NOT change any function behavior, signatures, or return types.
- Do NOT refactor working code. This is deletion and cleanup only.
- Do NOT remove the confirmation signal detection in `_should_offer_tools()`. The infrastructure stays for future use.
- Do NOT remove executor parameter schemas. They serve as documentation.
- Do NOT remove test files (test_moltbook*.py, extract_facts_test*.py, generate_test_data.py). Those are development tools, not dead code.

## How to Verify

1. Start the server — should start without errors
2. Send a message — should work normally
3. Trigger a search — should work normally  
4. Run: `grep -rn "_remove_live_chunks\|is_live\|get_tool_definitions\|CHUNK_SIZE\b\|CHUNK_OVERLAP\b" *.py` — should return zero results (except INGEST_ prefixed versions)
5. No `chat (1).py` should exist
