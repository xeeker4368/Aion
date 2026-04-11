# CC Task 85: Fix server.py None handling for hybrid search results

**Read this entire spec before making any changes.**

---

## Goal

Task 84 changed `memory.search()` to do hybrid search. The result dicts now contain `None` values for fields that came from only one search mechanism — for example, `distance` is `None` for chunks found by BM25 but missed by vector search.

`server.py` line 810 calls `round(c.get("distance", 0), 4)`, which crashes with `TypeError: type NoneType doesn't define __round__ method` when distance is None. The `.get()` default only fires when the key is missing — not when the value is None.

This task fixes that and any other places in `server.py` that have the same problem.

---

## File to Modify

Only `server.py`. Do not modify any other files.

---

## Change 1: Find the crash site

Find this block in `server.py` around line 810 (inside the `handle_chat` endpoint):

```python
            "distance": round(c.get("distance", 0), 4),
```

Replace it with:

```python
            "distance": round(c.get("distance") or 0, 4),
```

The `or 0` pattern handles both cases: missing key (returns None from .get(), then 0) and present-but-None key (None evaluates falsy, falls through to 0).

---

## Change 2: Check for other places with the same problem

Search `server.py` for any other occurrences of `round(c.get(` or `round(chunk.get(` or similar patterns that pull a value from a chunk dict and call `round()` on it.

Specifically grep for these patterns:

```
round(c.get(
round(chunk.get(
round(memory.get(
round(m.get(
```

For each occurrence found:

- If the field could be `None` from a BM25-only result (distance, weighted_distance, bm25_score, rrf_score, vector_rank, bm25_rank), apply the same `or 0` pattern.
- If you're not sure whether the field can be None, ask before making the change.

The fields that can be None in hybrid results:
- `distance` — None if vector search missed it
- `weighted_distance` — None if vector search missed it
- `bm25_score` — None if BM25 missed it
- `vector_rank` — None if vector search missed it
- `bm25_rank` — None if BM25 missed it

The fields that are always present:
- `text`
- `conversation_id`
- `source_type`
- `source_trust`
- `rrf_score`

---

## Change 3: Verify the response shape stays the same

The API response that the chat UI consumes should have the same field names as before. Don't add new fields. Don't remove existing fields. Only change how None values are handled when serializing for the response.

If a field is None, serialize it as 0 for numeric fields (the chat UI expects numbers there).

---

## What NOT to Do

- Do not modify `memory.py`. The hybrid search code is correct — it's the consumer that needs to handle the new shape.
- Do not modify the `search()` signature or return shape. Hybrid results are correct as they are.
- Do not add new fields to the API response.
- Do not remove fields from the API response.
- Do not change the order of fields in the response.
- Do not catch the TypeError and silently swallow it. Fix the actual cause.
- Do not modify any file other than `server.py`.

---

## Verification Steps

### Step 1: Restart the server in dev mode

```
./start.sh
# Pick option 2 (Dev Mode)
```

Confirm BM25 index still builds at startup.

### Step 2: Reproduce the crash scenario

The crash happened on request #7 in the previous session — sending a message that triggered retrieval where at least one returned chunk had no vector match (BM25-only result).

Send the chat sequence:

```
Hey there
Do you know who I am?
Do you remember the most important difference between you and Claude?
Sure, go ahead
```

The fourth message was the one that crashed previously. With the fix in place, it should return a normal response with no traceback in the server log.

### Step 3: Confirm no 500 errors

Watch the server log during the test. There should be no `Internal Server Error` lines and no Python tracebacks.

### Step 4: Confirm the chat UI displays results

The chat UI should show the response and the memory count without errors. If the UI shows undefined or NaN for distance, that's a separate UI bug to flag — but the server should not crash.

---

## Reporting Back

Report:
1. Did the four-message test sequence complete without errors?
2. Did all four responses come back to the UI normally?
3. Any other places in server.py that needed the same fix? List them.

---

*Spec written for Session 19 immediately after Task 84 verification revealed the integration bug.*
