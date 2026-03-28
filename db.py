"""
Aion Database Layer

Two SQLite databases:
- Archive: append-only, never modified, never deleted. Protects lived experiences.
- Working: same conversation data + metadata for processing state.

Every message writes to both simultaneously. The archive is the safety net.
If the working store gets corrupted, rebuild it from the archive.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import ARCHIVE_DB, WORKING_DB, DATA_DIR


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with row factory for dict-like access."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_databases():
    """Create both databases and their tables if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # --- Archive: minimal, sacred ---
    with _connect(ARCHIVE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_archive_conversation
            ON messages(conversation_id)
        """)

    # --- Working store: same data + processing metadata ---
    with _connect(WORKING_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                message_count INTEGER DEFAULT 0,
                chunked INTEGER DEFAULT 0,
                consolidated INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_working_conversation
            ON messages(conversation_id)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS summaries (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT,
                source_type TEXT NOT NULL DEFAULT 'article',
                source_trust TEXT NOT NULL DEFAULT 'thirdhand',
                chunk_count INTEGER DEFAULT 0,
                summarized INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        # Migration: add consolidated column if upgrading from Phase 1
        _migrate_working_db(conn)


def _migrate_working_db(conn):
    """Add columns that didn't exist in earlier versions."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "consolidated" not in columns:
        conn.execute(
            "ALTER TABLE conversations ADD COLUMN consolidated INTEGER DEFAULT 0"
        )


def start_conversation() -> str:
    """Start a new conversation. Returns the conversation ID."""
    conversation_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT INTO conversations (id, started_at) VALUES (?, ?)",
            (conversation_id, now),
        )
    return conversation_id


def end_conversation(conversation_id: str):
    """Mark a conversation as ended."""
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute(
            "UPDATE conversations SET ended_at = ? WHERE id = ?",
            (now, conversation_id),
        )


def save_message(conversation_id: str, role: str, content: str) -> dict:
    """
    Save a message to BOTH databases simultaneously.
    Returns the message as a dict.
    """
    message_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Write to both databases. If either fails, neither commits.
    archive_conn = _connect(ARCHIVE_DB)
    working_conn = _connect(WORKING_DB)

    try:
        archive_conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, conversation_id, role, content, now),
        )

        working_conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, conversation_id, role, content, now),
        )
        working_conn.execute(
            "UPDATE conversations SET message_count = message_count + 1 WHERE id = ?",
            (conversation_id,),
        )

        # Both succeeded — commit both
        archive_conn.commit()
        working_conn.commit()

    except Exception:
        archive_conn.rollback()
        working_conn.rollback()
        raise

    finally:
        archive_conn.close()
        working_conn.close()

    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "timestamp": now,
    }


def get_conversation_messages(conversation_id: str) -> list[dict]:
    """Get all messages for a conversation, ordered by time."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT id, conversation_id, role, content, timestamp "
            "FROM messages WHERE conversation_id = ? ORDER BY timestamp",
            (conversation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_conversation_message_count(conversation_id: str) -> int:
    """Get the current message count for a conversation."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT message_count FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return row["message_count"] if row else 0


def get_conversation_info(conversation_id: str) -> dict | None:
    """Get conversation metadata."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def mark_conversation_chunked(conversation_id: str):
    """Mark a conversation as fully chunked (final chunks created)."""
    with _connect(WORKING_DB) as conn:
        conn.execute(
            "UPDATE conversations SET chunked = 1 WHERE id = ?",
            (conversation_id,),
        )


def get_recent_conversations(limit: int = 20) -> list[dict]:
    """Get recent conversations, newest first."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_unchunked_ended_conversations() -> list[dict]:
    """Find conversations that ended but haven't been final-chunked yet."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM conversations "
            "WHERE ended_at IS NOT NULL AND chunked = 0",
        ).fetchall()
    return [dict(row) for row in rows]


def save_consolidation(conversation_id: str, summary: str):
    """
    Save consolidation summary for a conversation.
    Marks the conversation as consolidated.
    Facts are written to ChromaDB separately — not here.
    """
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        # Save summary
        summary_id = str(uuid.uuid4())
        conn.execute(
            "INSERT OR REPLACE INTO summaries (id, conversation_id, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (summary_id, conversation_id, summary, now),
        )

        # Mark conversation as consolidated
        conn.execute(
            "UPDATE conversations SET consolidated = 1 WHERE id = ?",
            (conversation_id,),
        )


def get_unconsolidated_conversations() -> list[dict]:
    """Find ended conversations that haven't been consolidated yet."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM conversations "
            "WHERE ended_at IS NOT NULL AND consolidated = 0",
        ).fetchall()
    return [dict(row) for row in rows]


def get_summary(conversation_id: str) -> str | None:
    """Get the summary for a conversation."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT content FROM summaries WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return row["content"] if row else None


def save_document(doc_id: str, title: str, url: str = None,
                  source_type: str = "article", source_trust: str = "thirdhand",
                  chunk_count: int = 0):
    """Record an ingested document for UI and batch processing."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, url, source_type, source_trust, "
            "chunk_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc_id, title, url, source_type, source_trust, chunk_count, now),
        )


def get_unsummarized_documents() -> list[dict]:
    """Find ingested documents that haven't been summarized yet."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE summarized = 0"
        ).fetchall()
    return [dict(row) for row in rows]


def mark_document_summarized(doc_id: str, summary: str):
    """Save summary and mark document as summarized."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect(WORKING_DB) as conn:
        summary_id = str(uuid.uuid4())
        conn.execute(
            "INSERT OR REPLACE INTO summaries (id, conversation_id, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (summary_id, doc_id, summary, now),
        )
        conn.execute(
            "UPDATE documents SET summarized = 1 WHERE id = ?",
            (doc_id,),
        )


def get_recent_summaries(limit: int = 5) -> list[dict]:
    """Get the most recent conversation summaries."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT s.conversation_id, s.content, s.created_at, c.started_at "
            "FROM summaries s "
            "JOIN conversations c ON s.conversation_id = c.id "
            "ORDER BY c.started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
