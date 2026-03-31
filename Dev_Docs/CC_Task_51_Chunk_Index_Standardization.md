# CC Task 51: Standardize Chunk Index Calculation

**Priority:** Before go-live
**Risk:** Low — logic doesn't change, only where it lives
**Files to modify:** memory.py, server.py, overnight.py

---

## The Problem

The chunk index formula is duplicated across 4 locations with two different-looking formulas:

**Live chunks** (at exact interval boundaries):
- `server.py:193` — `chunk_index = (message_count // LIVE_CHUNK_INTERVAL) - 1`

**Remainder chunks** (leftover messages at conversation end):
- `server.py:79` — `chunk_index = conv_msg_count // LIVE_CHUNK_INTERVAL`
- `server.py:159` — `chunk_index = msg_count // LIVE_CHUNK_INTERVAL`
- `overnight.py:56` — `chunk_index = msg_count // LIVE_CHUNK_INTERVAL`

These formulas produce correct sequential indices (0, 1, 2...) but the different-looking math makes the code fragile. Anyone modifying one formula without understanding the other creates silent bugs — masked by ChromaDB's `upsert` which overwrites without warning.

The fix: one helper function, one source of truth.

---

## The Fix

### Step 1: Add helper functions to memory.py

Add these two functions after `should_create_live_chunk()` (after line 86):

```python
def live_chunk_index(message_count: int) -> int:
    """
    Calculate the chunk index for a live chunk at an interval boundary.

    Chunk numbering is sequential starting from 0:
    - At message 10 (interval=10): index 0 (messages 1-10)
    - At message 20: index 1 (messages 11-20)
    - At message 30: index 2 (messages 21-30)
    """
    return (message_count // LIVE_CHUNK_INTERVAL) - 1


def remainder_chunk_index(message_count: int) -> int:
    """
    Calculate the chunk index for remainder messages at conversation end.

    The remainder chunk gets the next index after the last live chunk:
    - 15 messages (last live chunk at 10 was index 0): remainder is index 1
    - 25 messages (last live chunk at 20 was index 1): remainder is index 2
    """
    return message_count // LIVE_CHUNK_INTERVAL
```

### Step 2: Update server.py — _maybe_create_live_chunk (line 193)

**Current:**
```python
        chunk_index = (message_count // LIVE_CHUNK_INTERVAL) - 1
```

**Replace with:**
```python
        chunk_index = memory.live_chunk_index(message_count)
```

### Step 3: Update server.py — _end_active_conversation (line 159)

**Current:**
```python
            chunk_index = msg_count // LIVE_CHUNK_INTERVAL
```

**Replace with:**
```python
            chunk_index = memory.remainder_chunk_index(msg_count)
```

### Step 4: Update server.py — lifespan startup (line 79)

**Current:**
```python
                chunk_index = conv_msg_count // LIVE_CHUNK_INTERVAL
```

**Replace with:**
```python
                chunk_index = memory.remainder_chunk_index(conv_msg_count)
```

### Step 5: Update overnight.py — _end_active_conversations (line 56)

**Current:**
```python
                chunk_index = msg_count // LIVE_CHUNK_INTERVAL
```

**Replace with:**
```python
                chunk_index = memory.remainder_chunk_index(msg_count)
```

---

## What NOT to Do

- Do NOT change the actual math. `live_chunk_index` must return `(count // interval) - 1`. `remainder_chunk_index` must return `count // interval`. These are correct.
- Do NOT change `create_live_chunk()`, `should_create_live_chunk()`, or any ChromaDB logic.
- Do NOT change the chunking interval or any message-fetching logic.
- Do NOT add any new chunk-index calculations anywhere — all future uses should call these two functions.

---

## Verification

```python
python -c "
from config import LIVE_CHUNK_INTERVAL
from memory import live_chunk_index, remainder_chunk_index

interval = LIVE_CHUNK_INTERVAL  # 10

# Live chunks at exact boundaries
assert live_chunk_index(10) == 0, f'Expected 0, got {live_chunk_index(10)}'
assert live_chunk_index(20) == 1, f'Expected 1, got {live_chunk_index(20)}'
assert live_chunk_index(30) == 2, f'Expected 2, got {live_chunk_index(30)}'

# Remainder chunks
assert remainder_chunk_index(5) == 0, f'Expected 0, got {remainder_chunk_index(5)}'
assert remainder_chunk_index(15) == 1, f'Expected 1, got {remainder_chunk_index(15)}'
assert remainder_chunk_index(25) == 2, f'Expected 2, got {remainder_chunk_index(25)}'

# Verify no collisions: live chunk at 10 is index 0, remainder at 15 is index 1
assert live_chunk_index(10) != remainder_chunk_index(15), 'Collision detected!'

# Verify sequential: live at 10=0, remainder at 15=1, live at 20=1 (replaces remainder if conversation continued)
assert live_chunk_index(20) == remainder_chunk_index(15), 'Expected same index for continuation'

print('All chunk index tests passed.')
"
```

Also confirm no raw chunk index math remains:
```bash
grep -n "// LIVE_CHUNK_INTERVAL" server.py overnight.py
# Should return NO results — all replaced by function calls
```
