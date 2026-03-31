# CC Task 52: Fix Tool Intent Detection (Updated)

**Priority:** Before go-live — entity fabricates API results when intent detection misses
**Risk:** Low — changes one function
**File to modify:** server.py

---

## The Problem

`_has_tool_intent()` uses a narrow keyword list that misses many ways the entity expresses tool intent. Observed failures:

- Entity writes "Making an HTTP request to..." → misses because `"http request"` (space) not in list
- Entity writes "Let me use the API to get..." → misses because "use the api" not in list
- Entity includes a Moltbook API URL in its response → misses entirely

When intent detection misses, Pass 2 never fires, the entity never calls the API, and it **fabricates results** — confidently listing submolts, posts, or agents that don't exist.

## The Fix

Replace the entire `_has_tool_intent()` function.

**Current code (lines 269-292):**
```python
def _has_tool_intent(response_text: str) -> bool:
    """
    Detect if the entity's response expresses intent to use a tool.

    The entity sees skill descriptions and knows what tools exist.
    When it wants to use one, it mentions it in its response.
    This checks for those signals.
    """
    if not response_text:
        return False

    text = response_text.lower()

    # Tool names the entity knows from skill descriptions
    tool_signals = [
        "web_search", "web_fetch", "http_request",
        "let me search", "let me look", "i'll search", "i'll look up",
        "i can search", "i can look up", "let me check",
        "i'll check moltbook", "let me check moltbook",
        "search for", "look up", "look that up",
        "search the web", "check the web",
    ]

    return any(signal in text for signal in tool_signals)
```

**Replace with:**
```python
def _has_tool_intent(response_text: str) -> bool:
    """
    Detect if the entity's response expresses intent to use a tool.

    The entity sees skill descriptions and knows what tools exist.
    When it wants to use one, it mentions it in its response.
    This checks for those signals.

    Must be broad enough to catch natural language variations.
    A miss here means the entity fabricates results instead of
    calling the real API.
    """
    if not response_text:
        return False

    text = response_text.lower()

    # Tool name signals (exact names and natural language variants)
    tool_signals = [
        "web_search", "web_fetch", "http_request", "http request",
        "let me search", "let me look", "i'll search", "i'll look up",
        "i can search", "i can look up", "let me check",
        "i'll check moltbook", "let me check moltbook",
        "search for", "look up", "look that up",
        "search the web", "check the web",
        "use the api", "call the api", "check the api",
        "making a request", "making an http",
    ]

    if any(signal in text for signal in tool_signals):
        return True

    # If the response contains any known API endpoint URL, that's intent
    if "moltbook.com/api/" in text:
        return True

    return False
```

---

## What NOT to Do

- Do NOT change the two-pass logic itself.
- Do NOT change any other function in server.py.
- Do NOT add `store_document` back to the signals — that was intentionally removed in Session 14.

## Verification

```python
python -c "
def _has_tool_intent(text):
    text = text.lower()
    tool_signals = [
        'web_search', 'web_fetch', 'http_request', 'http request',
        'let me search', 'let me look', \"i'll search\", \"i'll look up\",
        'i can search', 'i can look up', 'let me check',
        \"i'll check moltbook\", 'let me check moltbook',
        'search for', 'look up', 'look that up',
        'search the web', 'check the web',
        'use the api', 'call the api', 'check the api',
        'making a request', 'making an http',
    ]
    if any(s in text for s in tool_signals):
        return True
    if 'moltbook.com/api/' in text:
        return True
    return False

# Should ALL match
tests_match = [
    'Let me use the API to get the latest list',
    'Making an HTTP request to https://www.moltbook.com/api/v1/submolts',
    'I\\'ll search for that topic',
    'Let me check moltbook for new posts',
    'I can look up that information',
    'Let me call the api to check your feed',
]
for t in tests_match:
    assert _has_tool_intent(t), f'MISSED: {t[:50]}'
    print(f'  MATCH: {t[:60]}')

# Should NOT match
tests_no = [
    'That sounds interesting! Tell me more.',
    'I remember we talked about that yesterday.',
    'Hello! How are you doing today?',
]
for t in tests_no:
    assert not _has_tool_intent(t), f'FALSE POSITIVE: {t[:50]}'
    print(f'  SKIP:  {t[:60]}')

print('All tests passed.')
"
```
