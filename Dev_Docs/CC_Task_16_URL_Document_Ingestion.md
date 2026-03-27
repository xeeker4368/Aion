# CC Task 16: URL Document Ingestion

## What This Does

Lets the entity absorb web pages into its memory during conversation. You say "remember this article: [URL]" and the server fetches the page, chunks it into ChromaDB, and confirms. The entity can recall any part of it from that point forward.

Summaries are generated later by qwen3:14b during the 2am batch window — stored in DB2 for the UI only, never seen by the entity.

## How It Works

1. Server detects an ingestion signal (URL + intent to remember)
2. Server fetches the page using `web_fetch` executor
3. Text is split into chunks at paragraph boundaries
4. Each chunk is stored in ChromaDB with source metadata (type: article, trust: thirdhand)
5. A document record is created in DB2 (for the UI and for qwen3:14b to summarize later)
6. Confirmation injected into system prompt so the entity knows it happened

No LLM involved during ingestion. nomic-embed-text handles embeddings. qwen3:14b summarizes during the 2am batch.

## Files to Change

### `config.py`

Add:

```python
# --- Document Ingestion ---
INGEST_CHUNK_SIZE = 1500       # chars per chunk (roughly 375 tokens)
INGEST_CHUNK_OVERLAP = 200     # chars overlap between chunks
```

### `db.py`

Add a documents table for tracking ingested documents. This is for the UI and batch processing — the entity's memory is the chunks in ChromaDB.

Add to `init_databases()` inside the working DB section:

```python
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
```

Add three functions:

```python
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
```

### `memory.py`

Add two functions:

```python
def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    Split text into chunks at paragraph boundaries.

    Tries to break at double newlines (paragraphs), falls back to
    single newlines, falls back to hard cut at chunk_size.
    Each chunk overlaps with the next by `overlap` characters
    to preserve context across boundaries.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            chunks.append(text[start:])
            break

        # Try to break at a paragraph boundary
        break_point = text.rfind("\n\n", start, end)
        if break_point == -1 or break_point <= start:
            # Try single newline
            break_point = text.rfind("\n", start, end)
        if break_point == -1 or break_point <= start:
            # Hard cut
            break_point = end

        chunks.append(text[start:break_point])
        start = break_point - overlap
        if start < 0:
            start = 0

    return chunks


def ingest_document(doc_id: str, text: str, title: str,
                    source_type: str = "article",
                    source_trust: str = "thirdhand") -> int:
    """
    Chunk and embed a document into ChromaDB.

    Returns the number of chunks created.
    """
    from config import INGEST_CHUNK_SIZE, INGEST_CHUNK_OVERLAP

    chunks = chunk_text(text, INGEST_CHUNK_SIZE, INGEST_CHUNK_OVERLAP)

    for i, chunk_text_piece in enumerate(chunks):
        # Prepend title to first chunk for better embedding context
        text_to_store = chunk_text_piece
        if i == 0:
            text_to_store = f"{title}\n\n{chunk_text_piece}"

        create_live_chunk(
            conversation_id=doc_id,
            messages=[{"role": "system", "content": text_to_store, "timestamp": ""}],
            chunk_index=i,
            source_type=source_type,
            source_trust=source_trust,
        )

    return len(chunks)
```

### `server.py`

Add ingestion detection and execution. Two new functions, plus wire into `handle_chat()`.

New functions:

```python
import re as _re
import uuid as _uuid

def _detect_ingest(message: str) -> str | None:
    """
    Detect if the user wants to ingest a URL.
    Returns the URL if found, None otherwise.
    """
    msg = message.lower().strip()

    ingest_signals = [
        "remember this", "save this", "store this",
        "read this", "ingest this", "absorb this",
        "add this to your memory", "add this to memory",
        "remember this article", "remember this page",
        "save this article", "save this page",
        "read and remember", "learn this",
    ]

    has_signal = any(signal in msg for signal in ingest_signals)
    if not has_signal:
        return None

    # Extract URL from the message
    url_pattern = r'https?://[^\s<>"\']+' 
    match = _re.search(url_pattern, message)
    if match:
        return match.group(0).rstrip(".,;:!?)")
    return None


def _ingest_url(url: str) -> str:
    """
    Fetch a URL, chunk it, store in ChromaDB, record in DB2.
    Returns a confirmation string to inject into the system prompt.
    """
    # Fetch the page (use a larger limit than default for ingestion)
    content = executors.execute("web_fetch", {"url": url, "max_chars": 20000})

    if content.startswith("Failed to fetch"):
        return f"Could not fetch {url}: {content}"

    if len(content.strip()) < 100:
        return f"The page at {url} had very little readable content."

    # Extract a title from the first line or use the URL
    lines = content.strip().split("\n")
    title = lines[0][:120] if lines else url

    # Create a document ID and store
    doc_id = str(_uuid.uuid4())

    # Chunk and embed into ChromaDB
    chunk_count = memory.ingest_document(
        doc_id=doc_id,
        text=content,
        title=title,
        source_type="article",
        source_trust="thirdhand",
    )

    # Record in DB2 for UI and batch summarization
    db.save_document(
        doc_id=doc_id,
        title=title,
        url=url,
        source_type="article",
        source_trust="thirdhand",
        chunk_count=chunk_count,
    )

    logger.info(
        f"Ingested {url}: {len(content)} chars, {chunk_count} chunks, doc_id={doc_id}"
    )

    return (
        f"Document saved to memory: \"{title}\" ({chunk_count} sections, "
        f"{len(content)} characters). You can now recall information from this "
        f"article in future conversations."
    )
```

Wire into `handle_chat()`. Add this BEFORE the tool execution block (step 4), after retrieval (step 3):

```python
    # 3b. Check for document ingestion
    ingest_result = None
    ingest_url = _detect_ingest(request.message)
    if ingest_url:
        ingest_result = _ingest_url(ingest_url)
        logger.info(f"Document ingestion: {ingest_url}")
```

Then pass `ingest_result` to `build_system_prompt()`. Update the call:

```python
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        skill_descriptions=skill_desc,
        search_results=search_results,
        ingest_result=ingest_result,
    )
```

### `chat.py`

Add `ingest_result` parameter to `build_system_prompt()`:

```python
def build_system_prompt(
    retrieved_chunks: list[dict],
    skill_descriptions: str = "",
    search_results: str = None,
    ingest_result: str = None,
) -> str:
```

Add this block after the search results section:

```python
    # --- Ingestion result (injected by server when a document was stored) ---
    if ingest_result:
        parts.append(
            f"\n\nA document was just saved to your memory: {ingest_result}"
        )
```

### `consolidation.py`

Add document summarization to the batch process. Add a new function:

```python
def summarize_documents():
    """Summarize any ingested documents that haven't been summarized yet."""
    pending = db.get_unsummarized_documents()

    if not pending:
        logger.info("No documents pending summarization.")
        return

    logger.info(f"Found {len(pending)} documents to summarize.")

    for doc in pending:
        doc_id = doc["id"]
        title = doc["title"]
        url = doc.get("url", "")

        # Get the chunks from ChromaDB for this document
        collection = None
        try:
            import memory
            memory.init_memory()
            collection = memory._get_collection()
            results = collection.get(
                where={"conversation_id": {"$eq": doc_id}},
            )
        except Exception as e:
            logger.error(f"Could not retrieve chunks for {doc_id}: {e}")
            continue

        if not results or not results["documents"]:
            logger.warning(f"No chunks found for document {doc_id}")
            continue

        # Combine chunk text for summarization
        full_text = "\n\n".join(results["documents"])

        prompt = (
            f"Read this document and write a 2-4 sentence summary of what it covers.\n"
            f"Title: {title}\n"
            f"URL: {url}\n\n"
            f"{full_text[:12000]}"  # Truncate to fit context window
        )

        logger.info(
            f"Summarizing document {doc_id} ({title}), "
            f"~{len(prompt) // 4} tokens"
        )

        try:
            client = ollama.Client(host=OLLAMA_HOST)
            response = client.chat(
                model=CONSOLIDATION_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"num_ctx": CONSOLIDATION_CTX},
            )
            summary = response["message"]["content"].strip()
        except Exception as e:
            logger.error(f"Summarization failed for {doc_id}: {e}")
            continue

        db.mark_document_summarized(doc_id, summary)
        logger.info(f"  Summarized: {title} -> {len(summary)} chars")
```

Update `consolidate_pending()` to also run document summarization:

```python
def consolidate_pending():
    """Find and process all pending conversations and documents."""
    # Conversations
    pending = db.get_unconsolidated_conversations()
    if not pending:
        logger.info("No conversations pending consolidation.")
    else:
        logger.info(f"Found {len(pending)} conversations to consolidate.")
        for conv in pending:
            result = consolidate_conversation(conv["id"])
            if result:
                logger.info(f"  ✓ {conv['id']}")
            else:
                logger.warning(f"  ✗ {conv['id']} failed")

    # Documents
    summarize_documents()
```

Update `__main__`:

```python
if __name__ == "__main__":
    """Run consolidation on all pending conversations and documents."""
    logging.basicConfig(level=logging.INFO)
    db.init_databases()
    consolidate_pending()
```

## What NOT to Do

- Do NOT use an LLM during ingestion. nomic-embed-text handles embeddings. Summarization happens in the 2am batch.
- Do NOT store the document in DB1 (archive). DB1 is for conversations. Documents go to ChromaDB (chunks) and DB2 (metadata/summary).
- Do NOT pass document text through the entity's context window. The server handles ingestion silently.
- Do NOT add file upload yet. URL ingestion first. File upload is a separate task.
- Do NOT modify the web_fetch executor. Use it as-is but pass a larger max_chars (20000) for ingestion.

## How to Verify

1. Start the server
2. Send: "remember this article: https://en.wikipedia.org/wiki/Llama_(language_model)"
3. Check the log for "Ingested [url]: X chars, Y chunks"
4. Entity should respond acknowledging the document was saved
5. Start a new conversation and ask something about the article — it should surface from ChromaDB
6. Run `python consolidation.py` — should summarize the document
7. Check DB2 summaries table for the document summary

## Token Budget Note

Ingestion does not use the entity's context window. The chunks go directly to ChromaDB via the server. The only thing entering the context is the short confirmation string ("Document saved to memory: ...").

The `web_fetch` call uses max_chars=20000 for ingestion (vs 4000 for search fetch chaining). This is fine because it never enters the entity's context — it goes straight to ChromaDB.
