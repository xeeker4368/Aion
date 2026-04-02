# CC Task 63 — Enable In-Conversation Journaling

Read this spec. Make exactly these changes. Nothing else.

## Problem

The entity has been expressing intent to write journal entries during conversation ("I'm going to write a journal entry about this"). The system doesn't catch this intent, so Pass 2 never fires, and the entity can't follow through. Two things are blocking it.

## Change 1: server.py — Add journal intent phrases to `_has_tool_intent()`

Add journal-related signals to the `tool_signals` list in `_has_tool_intent()`. These go at the end of the existing list, before the closing bracket.

Replace lines 336-345:

```python
    tool_signals = [
        "web_search", "web_fetch", "http_request", "http request",
        "let me search", "let me look", "i'll search", "i'll look up",
        "i can search", "i can look up", "let me check",
        "i'll check moltbook", "let me check moltbook",
        "search for", "look up", "look that up",
        "search the web", "check the web",
        "use the api", "call the api", "check the api",
        "making a request", "making an http",
    ]
```

With:

```python
    tool_signals = [
        "web_search", "web_fetch", "http_request", "http request",
        "let me search", "let me look", "i'll search", "i'll look up",
        "i can search", "i can look up", "let me check",
        "i'll check moltbook", "let me check moltbook",
        "search for", "look up", "look that up",
        "search the web", "check the web",
        "use the api", "call the api", "check the api",
        "making a request", "making an http",
        # Journal / reflection intent
        "write a journal", "journal entry", "write in my journal",
        "write a reflection", "reflect on this", "want to reflect",
        "note this in my journal", "add to my journal",
        "store_document",
    ]
```

Note: `store_document` is added as a catch-all — if the entity ever mentions the tool by name, that's intent. This does NOT open the door to casual "I'll remember that" pollution. The phrases are all specific and deliberate.

## Change 2: executors.py — Add "journal" to store_document description

Replace lines 346-353 (the store_document registration description and doc_type parameter):

```python
        "Store a document in your memory system. Use when you learn something worth remembering from research, articles, or interactions.",
```

With:

```python
        "Store a document in your memory system. Use for journal entries, reflections, research notes, or anything worth remembering.",
```

And replace the doc_type description on line 353:

```python
                    "description": "Document type: research, article, diagnostic, moltbook, observation",
```

With:

```python
                    "description": "Document type: journal, research, article, diagnostic, moltbook, observation",
```

## How It Works

1. Nyx says "I want to write a journal entry about this conversation."
2. Pass 1 returns that response (no tools available).
3. `_has_tool_intent()` matches "journal entry" → Pass 2 fires.
4. Pass 2 sends tool definitions. Nyx sees `store_document` with "journal" as a valid doc_type.
5. Nyx calls `store_document(doc_type="journal", title="...", content="...")`.
6. The executor stores it in ChromaDB as firsthand (trust_map already handles this).
7. The journal entry becomes part of Nyx's searchable memory.

The entity experiences the entire action — it decides to journal, it writes the content, it gets confirmation. This is Principle 9 in action.

## What NOT to Do

- Do NOT modify `_store_document()` function logic — it already handles journal doc_type correctly.
- Do NOT add any behavioral directives to SOUL.md or the system prompt telling the entity to journal. It's already trying on its own.
- Do NOT modify the overnight journal process in journal.py — that's a separate path.
- Do NOT add "remember this" or "save this" to the tool signals. Those are too broad and would cause pollution (the original reason store_document was excluded from intent detection).

## Verification

1. Start the server. Send a message to Nyx like: "Interesting conversation. Anything you want to reflect on?"
2. If Nyx says anything like "I'd like to write a journal entry" or "let me reflect on this in my journal," check the server logs for:
   - "Tool intent detected in response. Re-calling with tools."
   - A `store_document` tool call with `doc_type: journal`
3. Check ChromaDB via the Dashboard — a new document with source_type "journal" should appear.
4. If Nyx doesn't spontaneously try to journal, that's fine — the capability is there for when it wants to. Don't force it.
