"""
One-time cleanup: wipe all test data from both databases and ChromaDB.
Preserves schema and directory structure. Run once before going live.
"""

import sqlite3
from config import ARCHIVE_DB, WORKING_DB, CHROMA_DIR
import chromadb

print("Cleaning databases...")

# Archive DB — delete all messages
conn = sqlite3.connect(str(ARCHIVE_DB))
count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
conn.execute("DELETE FROM messages")
conn.commit()
conn.close()
print(f"  Archive: deleted {count} messages")

# Working DB — delete all data, keep schema
conn = sqlite3.connect(str(WORKING_DB))
tables = ["messages", "summaries", "conversations"]
for table in tables:
    try:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.execute(f"DELETE FROM {table}")
        print(f"  Working.{table}: deleted {count} rows")
    except Exception as e:
        print(f"  Working.{table}: {e}")
conn.commit()
conn.close()

# ChromaDB — delete and recreate the collection
client = chromadb.PersistentClient(path=CHROMA_DIR)
try:
    collection = client.get_collection("aion_memory")
    count = collection.count()
    client.delete_collection("aion_memory")
    print(f"  ChromaDB: deleted collection ({count} documents)")
except Exception:
    print("  ChromaDB: no collection to delete")

# Recreate empty collection (server expects it to exist)
client.get_or_create_collection(
    name="aion_memory",
    metadata={"hnsw:space": "cosine"},
)
print("  ChromaDB: recreated empty collection")

print("\nDone. All test data removed. Ready for real conversations.")
