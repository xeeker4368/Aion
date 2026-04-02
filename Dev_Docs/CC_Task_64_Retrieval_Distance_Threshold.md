# CC Task 64 — Retrieval Distance Threshold

Read this spec. Make exactly these changes. Nothing else.

## Problem

Memory search always returns 5 results regardless of relevance. If the entity asks about a topic with no relevant memories, it gets 5 unrelated chunks presented as "your own experiences and memories." With 93 chunks this is mostly harmless. As memories grow, this noise degrades the entity's responses.

## Design

Add a maximum distance cutoff. Chunks with weighted distance above the threshold are not returned, even if fewer than 5 results remain. Getting 2 relevant memories is better than 2 relevant + 3 noise.

The threshold applies to **weighted** distance (after trust weighting). This means firsthand memories get more benefit of the doubt on borderline relevance than thirdhand articles. A firsthand chunk at raw distance 0.7 gets weighted to 0.63 and might survive. A thirdhand chunk at the same raw distance gets weighted to 0.77 and gets cut.

Default threshold: 0.75. Conservative — only cuts obvious noise. Configurable through the Settings UI.

---

## Change 1: config.py — Add RETRIEVAL_MAX_DISTANCE

Add after line 64 (`RETRIEVAL_RESULTS = ...`):

```python
RETRIEVAL_MAX_DISTANCE = _overrides.get("RETRIEVAL_MAX_DISTANCE", 0.75)
```

---

## Change 2: config_manager.py — Make it editable

Add to the `EDITABLE_SETTINGS` dict, after the `RETRIEVAL_RESULTS` entry:

```python
    "RETRIEVAL_MAX_DISTANCE": {"default": 0.75, "type": "float"},
```

Also add float handling to the `update()` function's type coercion block. After the `elif meta["type"] == "string":` block (around line 86), add:

```python
    elif meta["type"] == "float":
        try:
            value = float(value)
        except (ValueError, TypeError):
            return False
```

---

## Change 3: memory.py — Apply threshold in search()

Add the import. Replace line 24:

```python
from config import (
    CHROMA_DIR,
    OLLAMA_HOST,
    EMBED_MODEL,
    LIVE_CHUNK_INTERVAL,
    RETRIEVAL_RESULTS,
)
```

With:

```python
from config import (
    CHROMA_DIR,
    OLLAMA_HOST,
    EMBED_MODEL,
    LIVE_CHUNK_INTERVAL,
    RETRIEVAL_RESULTS,
    RETRIEVAL_MAX_DISTANCE,
)
```

Then modify the dedup loop. Replace lines 321-332:

```python
    # Deduplicate — max one chunk per conversation
    seen_conversations = set()
    deduplicated = []
    for mem in memories:
        conv_id = mem["conversation_id"]
        if conv_id not in seen_conversations:
            seen_conversations.add(conv_id)
            deduplicated.append(mem)
        if len(deduplicated) >= n_results:
            break

    return deduplicated
```

With:

```python
    # Filter by distance threshold and deduplicate — max one chunk per conversation
    seen_conversations = set()
    deduplicated = []
    for mem in memories:
        # Stop if beyond relevance threshold
        if mem["weighted_distance"] is not None and mem["weighted_distance"] > RETRIEVAL_MAX_DISTANCE:
            break  # Sorted by distance, so everything after is worse

        conv_id = mem["conversation_id"]
        if conv_id not in seen_conversations:
            seen_conversations.add(conv_id)
            deduplicated.append(mem)
        if len(deduplicated) >= n_results:
            break

    return deduplicated
```

Note: `break` not `continue` — the list is sorted by weighted_distance, so once we hit a result beyond the threshold, everything after is worse. No point continuing.

---

## What NOT to Do

- Do NOT change the trust weights.
- Do NOT change the fetch_count multiplier (3x).
- Do NOT add gap detection or dynamic count — that's a separate future change.
- Do NOT change how distances are logged in server.py — the existing logging will show the threshold working (fewer chunks returned when nothing is relevant).

## Verification

1. Start the server. Send a message about a topic Nyx has discussed before (e.g., "Tell me about your name"). Check debug pills — should return relevant chunks with distances well under 0.75.
2. Send a message about a topic Nyx has never discussed (e.g., "What do you think about deep sea fishing?"). Check debug pills — should return fewer than 5 chunks (or possibly zero) since nothing relevant exists.
3. Check Settings page — `RETRIEVAL_MAX_DISTANCE` should appear as an editable field with default 0.75.
4. Change the value to 0.3 in Settings, restart the server, send a message — should return very few or zero chunks (only extremely close matches survive 0.3). Reset to 0.75 after testing.
