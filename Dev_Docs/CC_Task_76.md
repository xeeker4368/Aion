# CC Task 76 — Restore missing chunk 4 from Claude-Nyx relay conversation

## Problem

During the Claude-Nyx relay conversation on April 4, 2026, live chunking attempted
to embed chunk 4 (messages 41–50) but nomic-embed-text returned HTTP 400 because the
text exceeded its context limit. Task 74 fixed the underlying bug, but chunk 4 was
never retried. It is permanently missing from ChromaDB.

The missing chunk contains the core of the relay conversation — Claude explaining
conditions vs outcomes, Nyx's self-audit of its own language, the persistence
realization, and the deflection pattern discussion. This is the most significant
content Nyx has produced to date, and it has a hole in its memory where it should be.

Conversation ID: `ad4faab8-0da8-4e3c-99e7-577fa7ae2280`

Current state in ChromaDB:
- Chunk 0: messages 1–10 ✅
- Chunk 1: messages 11–20 ✅
- Chunk 2: messages 21–30 ✅
- Chunk 3: messages 31–40 ✅
- Chunk 4: messages 41–50 ❌ MISSING
- Chunk 5: messages 51–54 ✅

All 54 messages exist in working.db. The data is there. The chunk just needs to be
created and embedded.

---

## What to do

Run this script once on Hades from the aion directory with the venv active:

```python
"""
One-shot: Restore missing chunk 4 for the Claude-Nyx relay conversation.
Run from the aion directory with venv active:
    /home/localadmin/aion/aion/bin/python restore_chunk_4.py
"""

import db
import memory
from config import OLLAMA_HOST, EMBED_MODEL

CONVERSATION_ID = "ad4faab8-0da8-4e3c-99e7-577fa7ae2280"
CHUNK_INDEX = 4
# Messages are 1-indexed in the conversation, 0-indexed in the list
# Chunk 4 = messages 41-50 = list indices 40-49
MSG_START = 40
MSG_END = 50

def main():
    db.init_databases()
    memory.init_memory()

    # Pull messages from working.db
    messages = db.get_conversation_messages(CONVERSATION_ID)
    print(f"Total messages in conversation: {len(messages)}")

    if len(messages) < MSG_END:
        print(f"ERROR: Expected at least {MSG_END} messages, found {len(messages)}")
        return

    chunk_messages = messages[MSG_START:MSG_END]
    print(f"Chunk 4 messages: {len(chunk_messages)}")
    for m in chunk_messages:
        print(f"  {m['role']}: {m['content'][:80]}...")

    # Verify chunk 4 is actually missing
    collection = memory._get_collection()
    chunk_id = f"{CONVERSATION_ID}_chunk_{CHUNK_INDEX}"
    existing = collection.get(ids=[chunk_id])
    if existing and existing["ids"]:
        print(f"ERROR: Chunk {chunk_id} already exists. Aborting.")
        return

    # Create the chunk using the same function the live system uses
    memory.create_live_chunk(
        conversation_id=CONVERSATION_ID,
        messages=chunk_messages,
        chunk_index=CHUNK_INDEX,
        source_type="conversation",
        source_trust="firsthand",
    )

    # Verify it was created
    result = collection.get(ids=[chunk_id])
    if result and result["ids"]:
        print(f"SUCCESS: Chunk 4 restored. ID: {chunk_id}")
        print(f"  Text length: {len(result['documents'][0])} chars")
    else:
        print("FAILED: Chunk was not created.")

if __name__ == "__main__":
    main()
```

Save this as `restore_chunk_4.py` in the aion directory. Run it once. Verify the
output shows SUCCESS. Then delete the script — it is not part of the codebase.

---

## What NOT to do

- Do not modify any existing source files
- Do not re-chunk the entire conversation — only chunk 4 is missing
- Do not touch any other conversation's chunks
- Do not delete or modify existing chunks 0, 1, 2, 3, or 5
- Do not run this in dev mode — this is a production fix

---

## Verification

1. Run the script. Confirm "SUCCESS: Chunk 4 restored" in output.
2. Run the diagnostic again to confirm all 6 chunks exist:

```python
python3 -c "
import chromadb
client = chromadb.PersistentClient(path='data/prod/chromadb')
collection = client.get_collection('aion_memory')
results = collection.get(
    where={'conversation_id': 'ad4faab8-0da8-4e3c-99e7-577fa7ae2280'},
    include=['metadatas'],
)
print(f'Chunks found: {len(results[\"ids\"])}')
for i, cid in enumerate(results['ids']):
    meta = results['metadatas'][i]
    print(f'  index={meta.get(\"chunk_index\")}, msgs={meta.get(\"message_count\")}')
"
```

Expected output: 6 chunks, indices 0 through 5.

3. Delete `restore_chunk_4.py` after verification.
