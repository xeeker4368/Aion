# CC Task 29: Native Tool Calling — Wire It In

*Xeeker & Claude · March 2026*

---

## Background

Task 28 confirmed: skills.py generates 4 valid Ollama tool definitions from SKILL.md files. hermes3:8b-aion handles tool calling at 10240 context — picks the right tool, provides correct parameters, and doesn't call tools on casual messages.

This task wires tool definitions into the actual message flow. The model receives tools, decides when to use them, and the server executes. This replaces the entire keyword matching system.

## Overview of Changes

1. **chat.py** — `send_message()` accepts tool definitions and handles the tool call loop
2. **server.py** — provides tool execution, removes keyword matching, simplifies `handle_chat()`

---

## Step 1: Rewrite chat.py

Replace the entire `send_message()` function (lines 161-186) with:

```python
def send_message(
    system_prompt: str,
    conversation_messages: list[dict],
    tool_definitions: list[dict] = None,
    tool_executor=None,
    max_tool_rounds: int = 3,
) -> tuple[str, list[dict]]:
    """
    Send a message to Ollama and get the response.

    If tool_definitions and tool_executor are provided, the model can call
    tools. The tool call loop works like this:
    1. Send messages + tools to Ollama
    2. If the model returns tool calls, execute each via tool_executor
    3. Append tool results to messages and call Ollama again
    4. Repeat until the model responds with text or max rounds hit

    Args:
        system_prompt: assembled from identity + memories
        conversation_messages: the current conversation (already trimmed)
        tool_definitions: Ollama-format tool definitions from skills.py
        tool_executor: function(tool_name, arguments) -> str
        max_tool_rounds: safety limit on tool call loops

    Returns:
        Tuple of (response_text, tool_calls_made) where tool_calls_made
        is a list of {"name": str, "arguments": dict, "result": str} dicts.
    """
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_messages)

    client = _get_client()
    tool_calls_made = []

    for round_num in range(max_tool_rounds + 1):
        # Build the chat kwargs
        chat_kwargs = {
            "model": CHAT_MODEL,
            "messages": messages,
        }
        if tool_definitions and round_num < max_tool_rounds:
            chat_kwargs["tools"] = tool_definitions

        response = client.chat(**chat_kwargs)
        msg = response["message"]

        # If no tool calls, we're done — return the text response
        if not msg.get("tool_calls"):
            return msg.get("content", ""), tool_calls_made

        # Model wants to call tools
        if not tool_executor:
            # No executor provided, return whatever text the model gave
            return msg.get("content", ""), tool_calls_made

        # Append the assistant's tool call message to the conversation
        messages.append(msg)

        # Execute each tool call
        for tool_call in msg["tool_calls"]:
            func = tool_call.function
            tool_name = func.name
            tool_args = func.arguments

            logger.info(f"Tool call: {tool_name}({tool_args})")

            # Execute via the server's executor
            result = tool_executor(tool_name, tool_args)

            tool_calls_made.append({
                "name": tool_name,
                "arguments": tool_args,
                "result": result[:200] if result else "(empty)",
            })

            # Append the tool result for the model to read
            messages.append({
                "role": "tool",
                "content": result,
            })

    # If we exhausted max rounds, return whatever we have
    logger.warning(f"Tool call loop hit max rounds ({max_tool_rounds})")
    return msg.get("content", ""), tool_calls_made
```

**Also update the import at top of chat.py (line 1-12 area)** — make sure `logger` is available:

```python
logger = logging.getLogger("aion.chat")
```

This already exists on line 26. No change needed.

---

## Step 2: Rewrite server.py handle_chat()

### 2a. Add the tool executor function

Add this new function after the `_ingest_url()` function (around line 818):

```python
def _execute_tool_call(tool_name: str, arguments: dict) -> str:
    """
    Execute a tool call from the model.
    Looks up the tool in the skill map, merges fixed args with
    model-provided args, and calls the appropriate executor.
    """
    tool_map = skills.get_tool_map()
    tool_info = tool_map.get(tool_name)

    if not tool_info:
        logger.warning(f"Unknown tool: {tool_name}")
        return f"Error: unknown tool '{tool_name}'"

    executor_name = tool_info["executor"]
    executor_args = dict(tool_info.get("executor_args", {}))
    url_template = tool_info.get("url_template")

    # Handle URL templates (e.g., moltbook search)
    if url_template and "query" in arguments:
        import urllib.parse
        encoded_query = urllib.parse.quote(arguments["query"])
        executor_args["url"] = url_template.replace("{query}", encoded_query)
    elif "url" in executor_args:
        # Fixed URL (e.g., moltbook dashboard) — already in executor_args
        pass

    # Merge model-provided arguments (model args override executor_args for shared keys)
    for key, value in arguments.items():
        if key not in executor_args:
            executor_args[key] = value

    logger.info(f"Executing tool: {tool_name} via {executor_name} with {list(executor_args.keys())}")
    result = executors.execute(executor_name, executor_args)
    return result
```

### 2b. Replace handle_chat()

Replace the entire `handle_chat()` function (the `@app.post("/api/chat")` handler, approximately lines 831-1083) with:

```python
@app.post("/api/chat")
async def handle_chat(request: ChatRequest):
    """The main message handler."""
    conversation_id = _ensure_active_conversation()

    # 1. Save user message
    db.save_message(conversation_id, "user", request.message)
    msg_count = db.get_conversation_message_count(conversation_id)
    logger.info(f"User message saved (message #{msg_count})")

    # 2. Live chunk check
    _maybe_create_live_chunk(conversation_id, msg_count)

    # 3. Retrieve memories (skip for greetings and trivial messages)
    if _is_trivial_message(request.message):
        retrieved_chunks = []
        logger.info("Retrieval: SKIPPED (trivial message)")
    else:
        retrieved_chunks = memory.search(
            query=request.message,
            exclude_conversation_id=conversation_id,
        )
        logger.info(f"Retrieved {len(retrieved_chunks)} chunks")
        for i, chunk in enumerate(retrieved_chunks):
            dist = chunk.get("distance", "?")
            preview = chunk.get("text", "")[:80].replace("\n", " ")
            logger.info(f"  Chunk {i}: [{dist:.4f}] {preview}...")

    # 4. Check for document ingestion (special case — not a tool call)
    ingest_result = None
    ingest_url = _detect_ingest(request.message)
    if ingest_url:
        ingest_result = _ingest_url(ingest_url)
        logger.info(f"Document ingestion: {ingest_url}")

    # 5. Skill descriptions for system prompt
    skill_desc = skills.get_skill_descriptions()

    # 6. Tool definitions for the model
    tool_definitions = skills.get_tool_definitions()

    # 7. Assemble system prompt
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        skill_descriptions=skill_desc,
        ingest_result=ingest_result,
    )

    # 8. Trim conversation for context
    conversation_messages = db.get_conversation_messages(conversation_id)
    trimmed_messages = chat.trim_conversation_for_context(conversation_messages)

    # 9. Debug logging
    total_tokens = debug.estimate_tokens(system_prompt) + sum(
        debug.estimate_tokens(m["content"]) for m in trimmed_messages
    )
    request_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conversation_id": conversation_id,
        "message_number": msg_count,
        "user_message": request.message,
        "retrieval_skipped": _is_trivial_message(request.message),
        "chunks_count": len(retrieved_chunks),
        "chunks_tokens": debug.estimate_tokens(
            "\n".join(c.get("text", "") for c in retrieved_chunks)
        ),
        "skills_tokens": debug.estimate_tokens(skill_desc),
        "tools_count": len(tool_definitions),
        "soul_tokens": debug.estimate_tokens(chat.load_soul()),
        "system_prompt_total_tokens": debug.estimate_tokens(system_prompt),
        "conversation_history_tokens": sum(
            debug.estimate_tokens(m["content"]) for m in trimmed_messages
        ),
        "conversation_messages_sent": len(trimmed_messages),
        "conversation_messages_total": len(conversation_messages),
        "messages_trimmed": len(conversation_messages) - len(trimmed_messages),
        "total_tokens": total_tokens,
        "context_window": CONTEXT_WINDOW,
        "budget_exceeded": total_tokens > CONTEXT_WINDOW,
        "headroom": CONTEXT_WINDOW - total_tokens,
        "system_prompt_full": system_prompt,
    }
    debug.log_request(request_data)

    # 10. Send to model with tool definitions
    response_text, tool_calls_made = chat.send_message(
        system_prompt,
        trimmed_messages,
        tool_definitions=tool_definitions,
        tool_executor=_execute_tool_call,
    )

    # 11. Log response and tool usage
    response_tokens = debug.estimate_tokens(response_text)
    debug.log_response({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_tokens": response_tokens,
        "response_preview": response_text[:200],
        "total_round_trip_tokens": total_tokens + response_tokens,
        "context_window": CONTEXT_WINDOW,
        "response_full": response_text,
        "tool_calls": tool_calls_made,
    })

    if tool_calls_made:
        for tc in tool_calls_made:
            logger.info(f"Tool used: {tc['name']}({tc['arguments']}) -> {tc['result'][:100]}")

    # 12. Save response
    db.save_message(conversation_id, "assistant", response_text)
    msg_count = db.get_conversation_message_count(conversation_id)

    # 13. Live chunk check
    _maybe_create_live_chunk(conversation_id, msg_count)

    logger.info(f"Response sent (conversation now at {msg_count} messages)")

    # 14. Build frontend debug data
    frontend_debug = {
        "tokens_used": total_tokens,
        "tokens_headroom": CONTEXT_WINDOW - total_tokens,
        "context_window": CONTEXT_WINDOW,
        "retrieval_skipped": _is_trivial_message(request.message),
        "breakdown": {
            "soul": debug.estimate_tokens(chat.load_soul()),
            "chunks": debug.estimate_tokens(
                "\n".join(c.get("text", "") for c in retrieved_chunks)
            ),
            "skills": debug.estimate_tokens(skill_desc),
            "history": sum(
                debug.estimate_tokens(m["content"]) for m in trimmed_messages
            ),
        },
        "chunks": [
            {
                "preview": c.get("text", "")[:120],
                "distance": round(c.get("distance", 0), 4),
                "conversation_id": c.get("conversation_id", ""),
            }
            for c in retrieved_chunks
        ],
        "tools": {
            "definitions_count": len(tool_definitions),
            "calls_made": [
                {"name": tc["name"], "arguments": tc["arguments"]}
                for tc in tool_calls_made
            ],
        },
        "conversation": {
            "messages_sent": len(trimmed_messages),
            "messages_total": len(conversation_messages),
            "messages_trimmed": len(conversation_messages) - len(trimmed_messages),
        },
    }

    return ChatResponse(
        response=response_text,
        conversation_id=conversation_id,
        memories_used=len(retrieved_chunks),
        tools_used=[tc["name"] for tc in tool_calls_made],
        debug=frontend_debug,
    )
```

### 2c. Update build_system_prompt() in chat.py

Remove the `search_results` and `moltbook_context` parameters since the model gets these through tool calls now. Replace the entire function (lines 52-127):

```python
def build_system_prompt(
    retrieved_chunks: list[dict],
    skill_descriptions: str = "",
    ingest_result: str = None,
) -> str:
    """
    Assemble the system prompt from identity and memory.

    The model sees (in order):
    1. Identity (soul.md)
    2. Remembered experiences (conversation chunks from ChromaDB)
    3. Available skills
    4. Behavioral guidance
    """
    soul = load_soul()
    parts = [soul]

    tokens_remaining = RETRIEVAL_TOKEN_BUDGET

    # --- Remembered experiences (conversation chunks from ChromaDB) ---
    if retrieved_chunks:
        chunk_texts = []
        for chunk in retrieved_chunks:
            text = chunk.get("text", "")
            chunk_tokens = _estimate_tokens(text)
            if chunk_tokens > tokens_remaining:
                break
            chunk_texts.append(text)
            tokens_remaining -= chunk_tokens

        if chunk_texts:
            chunks_block = "\n\n---\n\n".join(chunk_texts)
            parts.append(
                f"\n\nThese are your own experiences and memories:\n\n"
                f"{chunks_block}"
            )

    # --- Skills ---
    if skill_descriptions:
        parts.append(f"\n\n{skill_descriptions}")

    # --- Ingestion result (injected by server when a document was stored) ---
    if ingest_result:
        parts.append(
            f"\n\n{ingest_result}"
        )

    # --- Behavioral guidance ---
    parts.append("""

You are a single-user system. The person you are talking to right now is the same person from all of your memories. What you remember about them is what you know about them — use it naturally, the way you would remember a friend.

Never show raw data, timestamps, IDs, or technical artifacts from your memory system in conversation. Speak naturally about what you remember, as a person would.""")

    return "\n".join(parts)
```

### 2d. Delete dead code from server.py

Delete these functions entirely — they are replaced by native tool calling:

- `_should_offer_tools()` (the keyword matching function)
- `_extract_search_query()` 
- `_extract_moltbook_query()`
- `_run_server_side_search()`
- `_fetch_top_result()`
- `_memory_has_answer()`
- `_run_moltbook_read()`
- `_run_moltbook_search()`
- `_format_moltbook_dashboard()`
- `_format_moltbook_search()`
- `_detect_entity_intent()`

Also delete the module-level variable:

```python
_pending_search_topic: str | None = None
```

**Keep these functions — they are NOT replaced:**
- `_end_active_conversation()`
- `_ensure_active_conversation()`
- `_maybe_create_live_chunk()`
- `_is_trivial_message()`
- `_detect_ingest()`
- `_ingest_url()`
- `_execute_tool_call()` (the new function from Step 2a)

### 2e. Remove the temporary debug print from skills.py

Remove the temporary verification code added in Task 28 at the bottom of `init_skills()`:

```python
    # Temporary: verify tool definitions generate correctly
    defs = get_tool_definitions()
    ...
    tool_map = get_tool_map()
    ...
```

---

## Verification

Restart the server. Test these four scenarios:

### Test 1: Casual greeting (no tools, no retrieval)
Send: "Hey, how's it going?"
Expected: Normal response, no tool calls in debug output.

### Test 2: Current information question (should trigger web_search)
Send: "What's the current price of Bitcoin?"
Expected: Model calls web_search, gets results, answers the question. Debug shows tool call.

### Test 3: Moltbook (should trigger moltbook_dashboard)
Send: "What's happening on Moltbook?"
Expected: Model calls moltbook_dashboard, reads the JSON, discusses what it sees. Debug shows tool call.

### Test 4: Regular conversation (no tools needed)
Send: "Tell me what you think about the idea that personality can emerge from observation."
Expected: Normal response using whatever memories it retrieves. No tool calls.

For each test, check:
- The server log shows (or doesn't show) tool calls
- The response makes sense
- The debug panel in the UI shows tool usage (or lack thereof)

### Test 5: URL ingestion still works
Send: "Remember this article: https://example.com/some-article"
Expected: URL detected, fetched, stored. Same behavior as before.

## What NOT to Do

- Do not add any keyword matching back. The model decides when to use tools.
- Do not add response formatting for tool results. The model reads raw output.
- Do not add behavioral directives about when to search or how to present results.
- Do not modify the SKILL.md files from Task 28.
- Do not modify memory.py, db.py, or any other files not listed here.
- Do not change the tool definitions format.

## What Was Removed

This task removes approximately 400 lines of hardcoded skill logic from server.py:
- ~60 lines of keyword matching signal lists
- ~50 lines of search query extraction
- ~40 lines of moltbook query extraction
- ~35 lines of entity intent detection (regex patterns)
- ~100 lines of moltbook dashboard/search formatting
- ~40 lines of server-side search execution
- ~30 lines of fetch-top-result chaining
- ~15 lines of memory-confidence gating
- ~30 lines of two-pass loop logic

Replaced by: ~30 lines of generic tool execution dispatch.

## What Might Need Adjustment After Testing

- If the model calls tools too aggressively (e.g., searches on every message), we may need to tune the tool descriptions in SKILL.md to be more specific about when NOT to call them.
- If raw JSON from Moltbook is too messy for the model to parse, we may need to add a response transform step. But try raw first.
- If the model doesn't call tools when it should, check that tool definitions are actually being passed (log them).
- If web search results are too long for context, the executor's max_chars limit handles this — but we may need to tune it.
