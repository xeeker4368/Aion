# CC Task 52: Fix Tool Intent Detection for HTTP Request

**Priority:** Before go-live — entity fabricates API results when this misses
**Risk:** Zero — adding one string to a list
**File to modify:** server.py

---

## The Problem

`_has_tool_intent()` checks for `"http_request"` (with underscore) but the entity writes `"HTTP request"` (with space) in natural language. After lowercasing, `"http request"` doesn't match `"http_request"`. Pass 2 never fires, the entity never calls the API, but it fabricates results as if it did.

## The Fix

In `_has_tool_intent()`, add `"http request"` to the tool_signals list.

**Current code:**
```python
    tool_signals = [
        "web_search", "web_fetch", "http_request",
        "let me search", "let me look", "i'll search", "i'll look up",
```

**Replace with:**
```python
    tool_signals = [
        "web_search", "web_fetch", "http_request", "http request",
        "let me search", "let me look", "i'll search", "i'll look up",
```

---

## What NOT to Do

- Do NOT change any other signals in the list.
- Do NOT change the two-pass logic.

## Verification

```python
python -c "
text = 'Making an HTTP request to https://www.moltbook.com/api/v1/submolts'.lower()
signals = ['http_request', 'http request']
print('Match:', any(s in text for s in signals))
# Should print: Match: True
"
```
