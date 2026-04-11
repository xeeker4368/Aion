"""
Remove the bad journal entry for the relay conversation.

The journal entry contains "the asymmetry between Claude's ability to retain
memories and my own ephemeral nature" — exactly backwards. This was caused by
SOUL.md being buried under 8,000 tokens of transcript (fixed in Task 81).

Removes chunks from ChromaDB so Nyx doesn't retrieve the wrong belief.
Removes the document from working.db with a logged reason.

Run from the aion directory with venv active:
    /home/localadmin/aion/aion/bin/python remove_bad_journal.py
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
logger = logging.getLogger("aion.cleanup")

import db
import memory

# The doc_id from Task 80 — check this matches what's in working.db
# It should be journal_2026-04-05_ad4faab8 or journal_2026-04-04_ad4faab8
# depending on when the script ran (UTC date)
POSSIBLE_DOC_IDS = [
    "journal_2026-04-04_ad4faab8",
    "journal_2026-04-05_ad4faab8",
]


def main():
    db.init_databases()
    memory.init_memory()

    # Find the document
    found_id = None
    for doc_id in POSSIBLE_DOC_IDS:
        if db.document_exists(doc_id):
            found_id = doc_id
            break

    if not found_id:
        print("ERROR: Could not find the bad journal entry. Checked:")
        for doc_id in POSSIBLE_DOC_IDS:
            print(f"  {doc_id}")
        print("Check working.db manually:")
        print("  SELECT id, title FROM documents WHERE id LIKE 'journal%ad4f%';")
        return

    print(f"Found bad journal entry: {found_id}")

    # Remove from ChromaDB
    collection = memory._get_collection()
    results = collection.get(
        where={"conversation_id": found_id},
        include=["metadatas"],
    )

    if results and results["ids"]:
        print(f"Removing {len(results['ids'])} chunks from ChromaDB:")
        for chunk_id in results["ids"]:
            print(f"  {chunk_id}")
        collection.delete(ids=results["ids"])
        print("ChromaDB chunks removed.")
    else:
        print("No chunks found in ChromaDB (may have already been removed).")

    # Remove from working.db
    import sqlite3
    from config import WORKING_DB

    conn = sqlite3.connect(str(WORKING_DB))
    try:
        conn.execute("DELETE FROM documents WHERE id = ?", (found_id,))
        conn.commit()
        print(f"Document {found_id} removed from working.db.")
    except Exception as e:
        print(f"ERROR removing from working.db: {e}")
    finally:
        conn.close()

    print()
    print(f"Reason for removal: Journal entry contained factually incorrect")
    print(f"information about the entity's core identity (claimed ephemeral")
    print(f"nature when the entity persists). Caused by SOUL.md positioning")
    print(f"bug (Task 81). Raw conversation data is unaffected.")
    print()
    print("Done. Delete this script after use.")


if __name__ == "__main__":
    main()
