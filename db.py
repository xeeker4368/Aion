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
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import ARCHIVE_DB, WORKING_DB, DATA_DIR


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with row factory for dict-like access."""
    conn = sqlite3.connect(str(db_path), timeout=5)
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
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_archive_timestamp
            ON messages(timestamp)
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
            CREATE INDEX IF NOT EXISTS idx_conversations_started
            ON conversations(started_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_ended
            ON conversations(ended_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_working_timestamp
            ON messages(timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_source_type
            ON documents(source_type)
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                message_count INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS overnight_runs (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_seconds REAL,
                conversations_closed INTEGER DEFAULT 0,
                research_status TEXT DEFAULT 'skipped',
                research_summary TEXT,
                journal_status TEXT DEFAULT 'skipped',
                journal_summary TEXT,
                observer_status TEXT DEFAULT 'skipped',
                observer_summary TEXT,
                consolidation_status TEXT DEFAULT 'skipped',
                consolidation_summary TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS self_knowledge (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                observation_count INTEGER,
                journal_count INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS self_reviews (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                draft TEXT NOT NULL,
                review TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_self_reviews_message_id ON self_reviews(message_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_self_reviews_conversation_id ON self_reviews(conversation_id)"
        )
        # Migration: add consolidated column if upgrading from Phase 1
        _migrate_working_db(conn)


def _migrate_working_db(conn):
    """Add columns that didn't exist in earlier versions."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "consolidated" not in columns:
        conn.execute(
            "ALTER TABLE conversations ADD COLUMN consolidated INTEGER DEFAULT 0"
        )

    doc_columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "summary" not in doc_columns:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN summary TEXT"
        )
    if "content" not in doc_columns:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN content TEXT"
        )

    # Add self-knowledge columns to overnight_runs if upgrading
    on_columns = {row[1] for row in conn.execute("PRAGMA table_info(overnight_runs)")}
    if "self_knowledge_status" not in on_columns:
        conn.execute(
            "ALTER TABLE overnight_runs ADD COLUMN self_knowledge_status TEXT DEFAULT 'skipped'"
        )
        conn.execute(
            "ALTER TABLE overnight_runs ADD COLUMN self_knowledge_summary TEXT"
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


def get_active_conversations() -> list[dict]:
    """Get all conversations that haven't been ended."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE ended_at IS NULL"
        ).fetchall()
    return [dict(row) for row in rows]


def is_conversation_ended(conversation_id: str) -> bool:
    """Check if a conversation has been ended (by overnight or other process)."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT ended_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return row is not None and row["ended_at"] is not None


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


def save_self_review(
    message_id: str,
    conversation_id: str,
    draft: str,
    review: str,
) -> dict:
    """
    Store a draft and its review, linked to the final assistant message
    that was actually sent to the user. The message_id must already exist
    in the working.db messages table — call save_message first, then call
    this with the returned message id.

    Returns the self_review as a dict.
    """
    review_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute(
            """
            INSERT INTO self_reviews
                (id, message_id, conversation_id, draft, review, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (review_id, message_id, conversation_id, draft, review, now),
        )

    return {
        "id": review_id,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "draft": draft,
        "review": review,
        "created_at": now,
    }


def get_self_review_for_message(message_id: str) -> dict | None:
    """Get the self_review linked to a specific message, or None."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT * FROM self_reviews WHERE message_id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
    return dict(row) if row else None


def count_self_reviews() -> int:
    """Count total self_reviews stored."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute("SELECT COUNT(*) FROM self_reviews").fetchone()
    return row[0] if row else 0


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


def document_exists(doc_id: str) -> bool:
    """Check if a document with this ID already exists."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT 1 FROM documents WHERE id = ? LIMIT 1",
            (doc_id,),
        ).fetchone()
    return row is not None


def save_document(doc_id: str, title: str, url: str = None,
                  source_type: str = "article", source_trust: str = "thirdhand",
                  chunk_count: int = 0, content: str = None):
    """Record an ingested document with its full content for rebuild safety."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT INTO documents (id, title, url, source_type, source_trust, "
            "chunk_count, content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, title, url, source_type, source_trust, chunk_count, content, now),
        )


def get_unsummarized_documents() -> list[dict]:
    """Find ingested documents that haven't been summarized yet."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE summarized = 0"
        ).fetchall()
    return [dict(row) for row in rows]


def mark_document_summarized(doc_id: str, summary: str):
    """Save summary directly on the document and mark as summarized."""
    with _connect(WORKING_DB) as conn:
        conn.execute(
            "UPDATE documents SET summarized = 1, summary = ? WHERE id = ?",
            (summary, doc_id),
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


def get_conversations_ended_since(hours: int = 24) -> list[dict]:
    """Get conversations that ended within the last N hours."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()

    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM conversations "
            "WHERE ended_at IS NOT NULL AND ended_at > ? "
            "ORDER BY ended_at",
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_observation(conversation_id: str, content: str,
                     message_count: int) -> dict:
    """Save a personality observation for a single conversation."""
    obs_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO observations "
            "(id, conversation_id, content, message_count, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (obs_id, conversation_id, content, message_count, now),
        )

    return {
        "id": obs_id,
        "conversation_id": conversation_id,
        "content": content,
        "message_count": message_count,
    }


def get_all_observations() -> list[dict]:
    """Get all observations in chronological order. For the profile generator."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT o.*, c.started_at FROM observations o "
            "JOIN conversations c ON o.conversation_id = c.id "
            "ORDER BY c.started_at ASC"
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_observation() -> dict | None:
    """Get the most recent observation."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT o.*, c.started_at FROM observations o "
            "JOIN conversations c ON o.conversation_id = c.id "
            "ORDER BY c.started_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_observation_for_conversation(conversation_id: str) -> dict | None:
    """Check if a conversation has already been observed."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT * FROM observations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def get_documents_since(hours: int = 24) -> list[dict]:
    """Get documents ingested within the last N hours."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()

    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE created_at > ? ORDER BY created_at",
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_overnight_run(run_data: dict):
    """Save an overnight run record."""
    with _connect(WORKING_DB) as conn:
        conn.execute(
            "INSERT INTO overnight_runs "
            "(id, started_at, ended_at, duration_seconds, conversations_closed, "
            "research_status, research_summary, journal_status, journal_summary, "
            "observer_status, observer_summary, "
            "self_knowledge_status, self_knowledge_summary, "
            "consolidation_status, consolidation_summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_data["id"], run_data["started_at"], run_data.get("ended_at"),
                run_data.get("duration_seconds"), run_data.get("conversations_closed", 0),
                run_data.get("research_status", "skipped"), run_data.get("research_summary"),
                run_data.get("journal_status", "skipped"), run_data.get("journal_summary"),
                run_data.get("observer_status", "skipped"), run_data.get("observer_summary"),
                run_data.get("self_knowledge_status", "skipped"), run_data.get("self_knowledge_summary"),
                run_data.get("consolidation_status", "skipped"), run_data.get("consolidation_summary"),
            ),
        )


def get_overnight_runs(limit: int = 10) -> list[dict]:
    """Get recent overnight runs, newest first."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM overnight_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_overnight_run() -> dict | None:
    """Get the most recent overnight run."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT * FROM overnight_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def save_self_knowledge(content: str, observation_count: int,
                        journal_count: int) -> dict:
    """Save the current self-knowledge narrative, replacing any previous version."""
    now = datetime.now(timezone.utc).isoformat()

    with _connect(WORKING_DB) as conn:
        conn.execute("DELETE FROM self_knowledge")
        conn.execute(
            "INSERT INTO self_knowledge "
            "(id, content, observation_count, journal_count, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("current", content, observation_count, journal_count, now),
        )

    return {
        "id": "current",
        "content": content,
        "observation_count": observation_count,
        "journal_count": journal_count,
        "created_at": now,
    }


def get_latest_self_knowledge() -> dict | None:
    """Get the most recent self-knowledge narrative."""
    with _connect(WORKING_DB) as conn:
        row = conn.execute(
            "SELECT * FROM self_knowledge ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_self_knowledge_history(limit: int = 10) -> list[dict]:
    """Get recent self-knowledge narratives, newest first."""
    with _connect(WORKING_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM self_knowledge ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
