# CC Task 72 — Remove Observations from ChromaDB

Read this spec. Make exactly these changes. Nothing else.

## Problem

Observer characterizations are stored in both working.db AND ChromaDB. When the entity searches its memory, raw observer notes surface as "your own experiences and memories." The entity never wrote these — an external model did. The entity re-experiences the observer's clinical descriptions as lived experience, which caused it to re-open the settled naming decision tonight.

Observations are infrastructure input to the pattern recognizer, not entity experience. The entity receives self-knowledge through the curated narrative in the context window — that's the designed channel. Observations in ChromaDB are a second, uncontrolled channel that bypasses the synthesis.

## Change 1: observer.py — Remove ChromaDB ingestion

Find the block that stores observations in ChromaDB (the try-except block with `memory.ingest_document`). Delete the entire block. The section to remove looks like:

```python
        # Store in ChromaDB
        doc_id = f"observation_{conv_id}"
        try:
            memory.ingest_document(
                doc_id=doc_id,
                text=f"Behavioral observation:\n\n{characterization}",
                title="Personality observation",
                source_type="observation",
                source_trust="secondhand",
            )
        except Exception as e:
            logger.error(
                "Failed to store observation for %s in ChromaDB: %s. "
                "Observation saved to DB2 but not searchable.",
                conv_id, e,
            )
```

Also remove the `import memory` line at the top of the file if it's no longer used anywhere else. Check first — if memory is used elsewhere in the file, keep it.

## Change 2: observer.py — Remove memory import if unused

Check if `memory` is used anywhere else in observer.py besides the block being removed. If not, remove:

```python
import memory
```

## Change 3: Clean existing observation chunks from ChromaDB

Create and run this one-off cleanup. Can be done inline:

```bash
python3 -c "
import memory
memory.init_memory()
collection = memory._get_collection()
results = collection.get(where={'source_type': 'observation'}, include=[])
if results and results['ids']:
    collection.delete(ids=results['ids'])
    print(f'Deleted {len(results[\"ids\"])} observation chunks from ChromaDB')
else:
    print('No observation chunks found')
"
```

Run this ONCE on production after deploying the code change.

## What NOT to Do

- Do NOT remove observations from working.db. The pattern recognizer reads them from there.
- Do NOT change pattern_recognition.py — it reads from db.get_all_observations(), not ChromaDB.
- Do NOT change db.py — save_observation() still writes to working.db as before.
- Do NOT change the reobserve.py script's ChromaDB storage (the script should be deleted anyway).
- Do NOT remove the `memory` import from observer.py if it's used elsewhere in the file.

## Verification

1. Run the overnight on dev. Check logs — observer should still produce observations and store them in working.db, but NO "ingest_document" calls for observations.
2. Check ChromaDB: `python3 -c "import memory; memory.init_memory(); c = memory._get_collection(); r = c.get(where={'source_type': 'observation'}, include=[]); print(len(r['ids']), 'observation chunks')"` — should print 0.
3. Check working.db: `sqlite3 data/prod/working.db "SELECT COUNT(*) FROM observations;"` — should still have all observations.
4. Send a message about naming to Nyx. Check debug pills — retrieved chunks should NOT include any observation text.
