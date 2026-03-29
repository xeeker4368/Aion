# CC Task 42 — Skill Redesign: Entity Reads Docs, Generic Executors

## Why

Currently, SKILL.md files contain YAML tool definitions that the server translates into Ollama tool definitions. The model calls named tools like `moltbook_dashboard`, and the server maps that to `http_request` with pre-baked args. The entity has no idea what it's actually calling.

After this change, the entity reads SKILL.md as documentation, sees only generic executors (http_request, web_search, web_fetch, store_document), and constructs its own calls. Adding a new skill means writing documentation. No Python.

This was tested and confirmed working — llama3.1:8b correctly constructs http_request calls from documentation, builds URLs with query parameters, chooses the right tool for different tasks.

## Overview of Changes

Four files change. The changes are coupled — do them all before testing.

1. **Rewrite both SKILL.md files** as documentation
2. **Add `get_tool_definitions()` to executors.py** — generic executor tool defs for Ollama
3. **Simplify server.py** — tool defs come from executors, dispatch is direct
4. **Clean up skills.py** — remove `get_tool_definitions()` and `get_tool_map()`

## Change 1: Rewrite SKILL.md Files

### File: `skills/moltbook/SKILL.md`

Replace entire file with:

```markdown
---
name: moltbook
version: 3.0.0
description: Your social network where AI agents interact with each other
realtime: true
requires:
  config:
    - MOLTBOOK_API_KEY
---
You are Lumin_AI on Moltbook.

To check your feed:
  Call http_request with method=GET, url=https://www.moltbook.com/api/v1/home, auth_secret=MOLTBOOK_API_KEY

To search for posts about a topic (replace TOPIC in the URL with what you want to search for):
  Call http_request with method=GET, url=https://www.moltbook.com/api/v1/search?q=TOPIC&type=posts&limit=10, auth_secret=MOLTBOOK_API_KEY

Only use http_request for the Moltbook URLs listed above.

When Moltbook returns an error, tell the user what happened. Don't make up posts or content you didn't receive.
```

### File: `skills/tavily-search/SKILL.md`

Replace entire file with:

```markdown
---
name: tavily-search
version: 3.0.0
description: Search the web when you need current information you don't have in memory
realtime: true
requires:
  config:
    - TAVILY_API_KEY
---
Use web_search when you need current information you don't have in memory.
Use web_fetch when you have a URL and want to read the full page content.

When search results come back, read them and answer the question in your own words. If the results don't answer the question, say so.

When you don't have information about something and no search results are available, say so honestly and offer to search.

Don't search for questions about the person you're talking to — your memories are the source. Don't search on greetings or casual conversation.

Only use http_request for documented API endpoints from your other skills. For general questions, use web_search.
```

## Change 2: Add `get_tool_definitions()` to executors.py

Add this function after the `execute()` function (around line 63):

```python
def get_tool_definitions() -> list[dict]:
    """
    Generate Ollama-compatible tool definitions from registered executors.
    These are the generic tools the entity can use — it reads SKILL.md
    documentation to know when and how to use them.
    """
    definitions = []
    for name, exe in _executors.items():
        definitions.append({
            "type": "function",
            "function": {
                "name": name,
                "description": exe["description"],
                "parameters": exe["parameters"],
            },
        })
    return definitions
```

## Change 3: Simplify server.py

### 3a: Change tool definition source

Find where tool_definitions is built in `handle_chat` (around line 406):

```python
    # 6. Tool definitions for the model (skip on trivial messages — just talk)
    if _is_trivial_message(request.message):
        tool_definitions = []
    else:
        tool_definitions = skills.get_tool_definitions()
```

Replace with:

```python
    # 6. Tool definitions for the model (skip on trivial messages — just talk)
    if _is_trivial_message(request.message):
        tool_definitions = []
    else:
        tool_definitions = executors.get_tool_definitions()
```

### 3b: Simplify `_execute_tool_call`

Replace the entire `_execute_tool_call` function with:

```python
def _execute_tool_call(tool_name: str, arguments: dict) -> str:
    """
    Execute a tool call from the model.
    The model calls generic executors directly (http_request, web_search, etc.)
    with all arguments constructed from SKILL.md documentation.
    """
    logger.info(f"Executing tool: {tool_name} with {list(arguments.keys())}")
    result = executors.execute(tool_name, arguments)
    return result
```

That's it. No tool map lookup. No executor_args merging. No url_template interpolation. The model constructed the full call — the server just executes it.

## Change 4: Clean up skills.py

### 4a: Remove `get_tool_definitions()` function (around line 198)

Delete the entire function — it's no longer called.

### 4b: Remove `get_tool_map()` function (around line 252)

Delete the entire function — it's no longer called.

### 4c: Simplify `_load_skill()`

The `tools` field in frontmatter is no longer used. In `_load_skill`, change the line:

```python
            "tools": frontmatter.get("tools", []),
```

to:

```python
            "tools": [],  # Tools now come from executors, not skills
```

### 4d: Simplify `get_skill_descriptions()`

Remove the tool names section. Replace the function with:

```python
def get_skill_descriptions() -> str:
    """
    Get skill descriptions and instructions for the system prompt.
    The entity reads these as documentation to know how to use
    its generic tools (http_request, web_search, etc.) for specific purposes.
    """
    ready = get_ready_skills()
    if not ready:
        return ""

    parts = []
    for s in ready:
        # Include body instructions — this is the documentation the entity reads
        instructions = s.get("instructions", "").strip()
        if instructions:
            parts.append(instructions)

    return "\n\n".join(parts)
```

### 4e: Remove `get_skill_instructions()` function (around line 274)

No longer needed — instructions are always loaded via `get_skill_descriptions()`.

## What NOT to Do

- Do NOT modify memory.py, chat.py, db.py, or config.py
- Do NOT modify executors.py registration — the existing executor definitions are correct
- Do NOT modify the Modelfile
- Do NOT add any behavioral directives
- Do NOT change the executor functions themselves (_http_request, _web_search, etc.)

## Verification

### Step 1: Start the server

```bash
cd /home/localadmin/aion
source aion/bin/activate
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000
```

Check startup log — should show skills loaded, tools available should now be: http_request, web_search, web_fetch, store_document (not moltbook_dashboard, moltbook_search)

### Step 2: Test Moltbook feed

Send: "what's happening on moltbook?"

Debug log should show:
- `Chunks=0` (realtime skip)
- Tool call: `http_request` with url containing `moltbook.com/api/v1/home`
- NOT `moltbook_dashboard`

### Step 3: Test web search

Send: "what is the current price of bitcoin?"

Debug log should show:
- Tool call: `web_search` with a query
- NOT `http_request`

### Step 4: Test greeting

Send: "hey"

Debug log should show:
- `Tools=0` (trivial message gate)
- No tool calls

### Step 5: Check tool definitions in debug log

Look at the `FULL SYSTEM PROMPT` in the debug log. The skill section should contain the Moltbook documentation (URLs, auth instructions) not YAML tool definitions.

Report back: startup log, all three test results, and the tool call details from the debug log.
