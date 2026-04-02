# CC Task 59: File Upload

**Priority:** Next feature after go-live stabilization
**Risk:** Low — uses existing ingestion pipeline
**Files to modify:** server.py, static/index.html
**New dependencies:** `python-multipart` (already a FastAPI dependency for form data)

---

## Overview

Users can upload files through the chat UI. Files are extracted to text, chunked into ChromaDB, and recorded in DB2. The entity can then recall file content through normal retrieval. This uses the exact same pipeline as URL ingestion — the only difference is the input source.

Supported file types at launch:
- `.txt`, `.md`, `.py`, `.js`, `.json`, `.yaml`, `.yml`, `.csv`, `.html`, `.css` — plain text, read directly
- `.pdf` — requires `pdfplumber` (add to requirements.txt: `pdfplumber`)

---

## Backend Changes

### Add to requirements.txt:
```
pdfplumber
python-multipart
```

### Add upload endpoint to server.py:

Add these imports at the top:
```python
from fastapi import UploadFile, File, Form
```

Add the endpoint:
```python
@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    message: str = Form(default=""),
):
    """
    Upload a file to the entity's memory.
    Extracts text, chunks into ChromaDB, records in DB2.
    Returns a confirmation that can be injected into the next chat message.
    """
    filename = file.filename or "unknown"
    content_bytes = await file.read()
    
    # Extract text based on file type
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    
    text_extensions = {
        "txt", "md", "py", "js", "json", "yaml", "yml",
        "csv", "html", "css", "sh", "sql", "toml", "cfg",
        "log", "xml", "ini",
    }
    
    if ext in text_extensions:
        try:
            text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content_bytes.decode("latin-1")
            except Exception:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Could not decode {filename} as text."},
                )
    elif ext == "pdf":
        try:
            import pdfplumber
            import io
            pdf = pdfplumber.open(io.BytesIO(content_bytes))
            pages = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    pages.append(page_text)
            pdf.close()
            text = "\n\n".join(pages)
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"error": f"Failed to extract PDF text: {str(e)[:200]}"},
            )
    else:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported file type: .{ext}"},
        )
    
    if len(text.strip()) < 10:
        return JSONResponse(
            status_code=400,
            content={"error": f"File {filename} had very little readable content."},
        )
    
    # Determine source trust — source code is firsthand, everything else secondhand
    source_type = "article"
    source_trust = "secondhand"
    if ext == "py":
        source_type = "source_code"
        source_trust = "firsthand"
    
    # Use filename as title
    title = filename
    
    # Chunk and store — same path as URL ingestion
    doc_id = str(_uuid.uuid4())
    
    chunk_count = memory.ingest_document(
        doc_id=doc_id,
        text=text,
        title=title,
        source_type=source_type,
        source_trust=source_trust,
    )
    
    db.save_document(
        doc_id=doc_id,
        title=title,
        source_type=source_type,
        source_trust=source_trust,
        chunk_count=chunk_count,
    )
    
    logger.info(
        f"Uploaded {filename}: {len(text)} chars, {chunk_count} chunks, doc_id={doc_id}"
    )
    
    return {
        "status": "uploaded",
        "filename": filename,
        "chars": len(text),
        "chunks": chunk_count,
        "doc_id": doc_id,
        "message": f"File {filename} uploaded and saved to memory. {len(text)} characters, {chunk_count} sections.",
    }
```

---

## Frontend Changes

Add a file upload button next to the send button in the chat input area. When a file is selected, upload it immediately and show a confirmation message in the chat.

### In index.html, find the chat input row:

```html
<div class="chat-input-row">
    <textarea class="chat-input" id="chatInput" ...></textarea>
    <button class="send-btn" id="sendBtn" ...>...</button>
</div>
```

### Replace with:

```html
<div class="chat-input-row">
    <input type="file" id="fileInput" style="display:none;" onchange="uploadFile(this)">
    <button class="send-btn" onclick="document.getElementById('fileInput').click()" title="Upload file" style="background:var(--bg-hover);">
        <svg viewBox="0 0 24 24" style="stroke:var(--text-muted);"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
    </button>
    <textarea class="chat-input" id="chatInput" placeholder="Message..." rows="1" onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
    <button class="send-btn" id="sendBtn" onclick="sendMessage()">
        <svg viewBox="0 0 24 24"><path d="M22 2L11 13"/><path d="M22 2L15 22L11 13L2 9L22 2Z"/></svg>
    </button>
</div>
```

### Add the upload function to the script section:

```javascript
async function uploadFile(input) {
    const file = input.files[0];
    if (!file) return;
    input.value = ''; // reset for next upload

    addMessage('system-msg', `Uploading ${file.name}...`);

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetch('/api/upload', {
            method: 'POST',
            body: formData,
        });
        const data = await res.json();

        if (!res.ok) {
            addMessage('system-msg', `Upload failed: ${data.error || 'unknown error'}`);
            return;
        }

        addMessage('system-msg', data.message);

    } catch(e) {
        addMessage('system-msg', 'Upload failed: connection error');
        console.error(e);
    }
}
```

---

## What NOT to Do

- Do NOT change the existing URL ingestion path — it stays as is.
- Do NOT store the raw file bytes anywhere — only the extracted text goes to ChromaDB.
- Do NOT add file upload to the entity's tool definitions — this is a user action, not an entity action.
- Do NOT add any file size limits in the first version — keep it simple. If it becomes a problem, add limits later.
- Do NOT try to extract text from images, Word docs, or other binary formats at launch — just the text-based formats and PDF listed above.

---

## Verification

1. Install dependencies:
   ```bash
   pip install pdfplumber python-multipart --break-system-packages
   ```

2. Restart the server.

3. Upload a `.txt` file through the UI:
   - Click the paperclip button
   - Select a text file
   - Should see "Uploading filename..." then "File X uploaded and saved to memory. N characters, M sections."

4. Upload a `.py` file (e.g., one of the Aion source files):
   - Should succeed with source_type="source_code" and source_trust="firsthand"

5. Verify in ChromaDB:
   ```python
   python3 -c "
   import memory
   memory.init_memory()
   c = memory._get_collection()
   results = c.get(where={'source_type': 'source_code'}, include=['metadatas'])
   print(f'Source code chunks: {len(results[\"ids\"])}')
   for m in results['metadatas']:
       print(f'  {m}')
   "
   ```

6. Start a new conversation and ask about the uploaded content — retrieval should find it.

7. Upload a `.pdf` — should extract text and store correctly.

8. Upload an unsupported file type (e.g., `.zip`) — should return an error message.
