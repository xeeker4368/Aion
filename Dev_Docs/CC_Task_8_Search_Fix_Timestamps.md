# CC Task 8: Fix Search False Trigger + Add Console Timestamps

## What This Is

Two small fixes:
1. The keyword `"is there a"` in the search signal list is too broad — it matched "is there anything you're curious about?" and triggered a web search on a conversational question. Remove it.
2. Add a human-readable timestamp to the debug console output.

## Changes

### File 1: `server.py`

**Remove `"is there a"` from `info_signals`** in the `_should_offer_tools()` function. Find the `info_signals` list (approx line 228-236) and delete this line:

```python
        "is there a", "does there exist",
```

Replace with:

```python
        "does there exist",
```

That's it — just remove `"is there a"` from the list. Keep `"does there exist"`.

### File 2: `debug.py`

**Add a human-readable timestamp to the console request header.** In the `log_request` function, find (approx line 164-165):

```python
    console_lines = [
        f'--- REQUEST #{d["message_number"]} | {d["timestamp"]} ---',
```

Change to:

```python
    from datetime import datetime
    readable_time = datetime.fromisoformat(d["timestamp"]).strftime("%I:%M:%S %p")
    console_lines = [
        f'--- REQUEST #{d["message_number"]} | {readable_time} ---',
```

This changes the console output from:
```
--- REQUEST #3 | 2026-03-26T21:26:15.230301+00:00 ---
```
To:
```
--- REQUEST #3 | 09:26:15 PM ---
```

The full ISO timestamp is still in the debug log file — this only affects console output.

## What NOT To Do

- Do NOT remove any other search signals.
- Do NOT change the debug log file format — only console output.
- Do NOT touch anything else.

## Verification

1. Restart the server.
2. Send "is there anything you're curious about?" in the chat.
3. Console should show `Tool gate: CLOSED (no signals)` — no search triggered.
4. Console request header should show a readable time like `09:35:00 PM` instead of the ISO timestamp.
5. Send "search for Python 3.13" — should still trigger search normally.

## Done When

Conversational questions with "is there a" no longer trigger search, and console timestamps are human-readable.
