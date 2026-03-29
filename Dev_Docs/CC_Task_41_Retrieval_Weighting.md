# CC Task 41 — Retrieval Weighting: Source Trust and Duplicate Penalization

## Why

ChromaDB returns results ranked purely by cosine similarity — closest vector match wins. A fabricated Moltbook chunk and a real conversation chunk are treated equally. The entity's own lived experience should outweigh something it read in an article.

Also, if 3 of the 5 results come from the same conversation, they crowd out memories from other experiences. The entity gets a narrow view instead of a broad one.

Both fixes go into `memory.py` in the `search()` function.

## What to Change

**File:** `memory.py`

**Replace the `search` function with:**

```python
def search(query: str, n_results: int = RETRIEVAL_RESULTS,
           exclude_conversation_id: str = None) -> list[dict]:
    """
    Search memory for relevant chunks.

    Results are weighted by source trust (firsthand experience ranks
    higher than thirdhand articles) and deduplicated so no single
    conversation dominates the results.

    Returns a list of results, each with:
    - text: the chunk content
    - conversation_id: which conversation it came from
    - distance: how close the match is (lower = better)
    - weighted_distance: distance after trust weighting
    - source_type: what kind of experience
    - source_trust: how trustworthy
    """
    collection = _get_collection()

    if collection.count() == 0:
        return []

    # Request more results than needed so we have room after deduplication
    fetch_count = min(n_results * 3, collection.count())

    query_params = {
        "query_texts": [query],
        "n_results": fetch_count,
    }

    if exclude_conversation_id:
        query_params["where"] = {
            "conversation_id": {"$ne": exclude_conversation_id}
        }

    try:
        results = collection.query(**query_params)
    except Exception as e:
        import logging
        logging.getLogger("aion.memory").error(f"ChromaDB search failed: {e}")
        return []

    if not results or not results["documents"] or not results["documents"][0]:
        return []

    # Trust weighting — firsthand experience ranks higher
    trust_weights = {
        "firsthand": 0.9,    # boost — conversations with Lyle
        "secondhand": 1.0,   # neutral — research, observations
        "thirdhand": 1.1,    # penalty — articles, other AIs
    }

    memories = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        raw_distance = results["distances"][0][i] if results["distances"] else None

        trust = meta.get("source_trust", "firsthand")
        weight = trust_weights.get(trust, 1.0)
        weighted_distance = raw_distance * weight if raw_distance is not None else None

        memories.append({
            "text": doc,
            "conversation_id": meta.get("conversation_id", ""),
            "distance": raw_distance,
            "weighted_distance": weighted_distance,
            "source_type": meta.get("source_type", "conversation"),
            "source_trust": meta.get("source_trust", "firsthand"),
        })

    # Sort by weighted distance (lower = better)
    memories.sort(key=lambda m: m["weighted_distance"] if m["weighted_distance"] is not None else float("inf"))

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

## What NOT to Do

- Do NOT modify any other function in memory.py
- Do NOT modify ChromaDB metadata or chunk storage
- Do NOT change chunk creation or ingestion
- Do NOT modify config.py — the trust weights live in the function for now, easy to tune later

## Verification

```bash
cd /home/localadmin/aion
source aion/bin/activate

python3 -c "
import memory
memory.init_memory()

results = memory.search('hello')
print(f'Results: {len(results)}')
for r in results:
    conv = r['conversation_id'][:8]
    print(f'  [{r[\"weighted_distance\"]:.4f}] trust={r[\"source_trust\"]} type={r[\"source_type\"]} conv={conv}...')
    # Check dedup
convs = [r['conversation_id'] for r in results]
assert len(convs) == len(set(convs)), 'FAIL: duplicate conversations in results'
print('PASS: no duplicate conversations')
"
```

Then test via the chat UI — send a message and check the debug log. Chunk distances in the log will still show the raw distance. The weighted distance is used internally for ranking.

## Post-Go-Live Retrieval Tuning

The following retrieval tuning tasks need real accumulated data to calibrate. They are NOT blockers for go-live. Do them after the entity has been running for at least a week with real conversations:

- **Distance thresholds** — set a cutoff so very distant chunks (e.g., > 0.8) are excluded. Requires reviewing debug logs to see what "too distant" looks like with real data.
- **Topic shift detection** — detect when the conversation changes subject and re-search instead of carrying stale context. Requires conversation patterns to evaluate.
- **Chunk size tuning** — currently 10 messages per live chunk, 1500 chars for ingested docs. Adjust based on retrieval quality observations.
- **Retrieval count tuning** — fixed at 5 results. May need more or fewer depending on conversation depth and context window usage.
- **Trust weight tuning** — the 0.9/1.0/1.1 values are a starting point. Adjust based on whether the right memories surface in practice.
