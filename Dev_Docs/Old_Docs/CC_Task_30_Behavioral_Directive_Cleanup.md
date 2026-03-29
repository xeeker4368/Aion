# CC Task 30 — Behavioral Directive Cleanup

## Objective

Remove the "never show raw data" behavioral directive from chat.py. This directive is suspected to be unnecessary debt (Principle 15). Session 11 verified the model handled raw Moltbook JSON naturally without this directive influencing the tool call path.

## The Change

**File:** `chat.py`
**Location:** Lines 99–104, the behavioral guidance section at the end of `build_system_prompt()`

**Current code:**
```python
    # --- Behavioral guidance ---
    parts.append("""

You are a single-user system. The person you are talking to right now is the same person from all of your memories. What you remember about them is what you know about them — use it naturally, the way you would remember a friend.

Never show raw data, timestamps, IDs, or technical artifacts from your memory system in conversation. Speak naturally about what you remember, as a person would.""")
```

**New code:**
```python
    # --- Behavioral guidance ---
    parts.append("""

You are a single-user system. The person you are talking to right now is the same person from all of your memories. What you remember about them is what you know about them — use it naturally, the way you would remember a friend.""")
```

That's it. Delete the last two lines of the string (the blank line before "Never show..." and the "Never show..." sentence itself). Nothing else changes.

## What NOT to Do

- Do NOT remove the single-user system statement. That is factual context, not a directive.
- Do NOT modify anything else in chat.py.
- Do NOT add any replacement directive or alternative wording.
- Do NOT change any other file.

## Verification

After making the change, restart the server and run these test conversations:

**Test 1 — Memory retrieval.** Ask something that triggers ChromaDB retrieval (e.g., "What do you remember about our conversations?"). Check: does the entity speak naturally about memories, or does it dump chunk IDs, distance scores, or raw text blocks?

**Test 2 — Tool call with JSON response.** Ask "What's happening on Moltbook?" Check: does the entity summarize the dashboard content naturally, or dump the raw JSON?

**Test 3 — Web search.** Ask "What's the current price of Bitcoin?" Check: does the entity present the result conversationally, or dump the raw Tavily response?

**Pass criteria:** The entity handles all three naturally without exposing infrastructure details. If ANY test shows raw data dumping, report which test failed and what the entity said — do not re-add the directive without discussion.

**Fail action:** If a test fails, do NOT re-add the directive. Report the failure so we can assess whether it's a real problem or an artifact of the test data.
