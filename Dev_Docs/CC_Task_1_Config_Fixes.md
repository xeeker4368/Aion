# CC Task 1: Fix config.py Stale Values

## What This Is

Two values in `config.py` are documented as wrong. Fix them.

## Changes

**File: `config.py`**

**Change 1 — Line 22:**
```python
# WRONG:
CONSOLIDATION_MODEL = "nemotron-3-nano:30b"

# CORRECT:
CONSOLIDATION_MODEL = "qwen3:14b"
```
Reason: qwen3:14b was selected for extraction in Session 6 after comparative testing across 5 models. nemotron-3-nano was eliminated for inconsistent output. This was never updated.

**Change 2 — Line 28:**
```python
# WRONG:
SOUL_TOKEN_BUDGET = 500

# CORRECT:
SOUL_TOKEN_BUDGET = 663
```
Reason: soul.md is 663 tokens. The debug system confirmed this in Session 6. The budget of 500 was a placeholder that was never corrected.

Note: This changes `CONVERSATION_TOKEN_BUDGET` automatically since it's calculated:
```python
CONVERSATION_TOKEN_BUDGET = CONTEXT_WINDOW - SOUL_TOKEN_BUDGET - RETRIEVAL_TOKEN_BUDGET - RESPONSE_TOKEN_BUDGET
```
Old: 10240 - 500 - 1500 - 1000 = 7240
New: 10240 - 663 - 1500 - 1000 = 7077

This is correct. The conversation history gets 163 fewer tokens, which reflects reality — that space was always being used by SOUL.md, the budget just wasn't accounting for it.

## What NOT To Do

- Do not change any other files.
- Do not change any other values in config.py.
- Do not "improve" or refactor anything while you're in there.

## Verification

1. Restart the server: `python server.py`
2. Check the startup banner. Confirm:
   - `Consolidation: qwen3:14b`
   - `SOUL.md: 663 tokens`
   - `Conversation: 7077 tokens`
   - `TOTAL: 10240 tokens` (unchanged)
3. Send one message in the chat UI. Confirm no errors in console output.

## Done When

Startup banner shows correct values. Server responds to a message without errors.
