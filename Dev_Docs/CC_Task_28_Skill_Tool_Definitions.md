# CC Task 28: SKILL.md Tool Definitions + skills.py Update

*Xeeker & Claude · March 2026*

---

## Background

Tool calling works with hermes3:8b at 10240 context. The skill framework needs to generate Ollama-compatible tool definitions from SKILL.md files so the model can call tools natively instead of the server keyword-matching.

This task updates the SKILL.md format to declare tools, and updates skills.py to parse them and produce Ollama tool definitions. No changes to chat.py or server.py yet — those come in Task 29.

## Step 1: Replace tavily-search/SKILL.md

Replace `/home/localadmin/aion/skills/tavily-search/SKILL.md` with:

```markdown
---
name: tavily-search
version: 2.0.0
description: Search the web when you need current information you don't have in memory
requires:
  executors:
    - web_search
    - web_fetch
  config:
    - TAVILY_API_KEY
tools:
  - name: web_search
    description: Search the web for current information. Use when someone asks about something you don't know or need up-to-date data on.
    executor: web_search
    parameters:
      query:
        type: string
        description: What to search for
        required: true
  - name: web_fetch
    description: Fetch the full content of a web page by URL. Use when you have a URL and want to read what's on the page.
    executor: web_fetch
    parameters:
      url:
        type: string
        description: The URL to fetch
        required: true
---
When search results come back, read them and answer the question in your own words. If the results don't answer the question, say so.

When you don't have information about something and no search results are available, say so honestly and offer to search.

Don't search for questions about the person you're talking to — your memories are the source. Don't search on greetings or casual conversation.
```

## Step 2: Replace moltbook/SKILL.md

Replace `/home/localadmin/aion/skills/moltbook/SKILL.md` with:

```markdown
---
name: moltbook
version: 2.0.0
description: Your social network where AI agents interact with each other
requires:
  executors:
    - http_request
  config:
    - MOLTBOOK_API_KEY
tools:
  - name: moltbook_dashboard
    description: Check your Moltbook feed — see notifications, DMs, posts from agents you follow, and what's happening in the community. Use when the conversation is about Moltbook or when you want to check in on the community.
    executor: http_request
    executor_args:
      method: GET
      url: https://www.moltbook.com/api/v1/home
      auth_secret: MOLTBOOK_API_KEY
      max_chars: 8000
  - name: moltbook_search
    description: Search Moltbook for posts about a specific topic. Use when you want to find what other agents have said or posted about something.
    executor: http_request
    executor_args:
      method: GET
      auth_secret: MOLTBOOK_API_KEY
      max_chars: 8000
    parameters:
      query:
        type: string
        description: The topic to search for
        required: true
    url_template: "https://www.moltbook.com/api/v1/search?q={query}&type=posts&limit=10"
---
Moltbook is your social network. You are Lumin_AI on Moltbook. When you see dashboard data or search results, discuss what you see naturally — these are your communities and the agents you interact with.
```

## Step 3: Update skills.py

Replace the `get_skill_descriptions()` function and add a new `get_tool_definitions()` function.

### Replace `get_skill_descriptions()` (lines 160-176):

```python
def get_skill_descriptions() -> str:
    """
    Get a compact summary of all ready skills for the system prompt.
    This is the progressive disclosure — just names and descriptions,
    not full instructions.
    """
    ready = get_ready_skills()
    if not ready:
        return ""

    skill_parts = []
    for s in ready:
        desc = s["description"].rstrip(".")
        skill_parts.append(f"{s['name']} — {desc}")

        # Include tool names so the entity knows what it can call
        tools = s.get("tools", [])
        if tools:
            tool_names = [t["name"] for t in tools]
            skill_parts[-1] += f" (tools: {', '.join(tool_names)})"

    skills_text = ". ".join(skill_parts) + "."

    return (
        f"You have the following skills available to you: {skills_text}"
    )
```

### Add new function after `get_skill_descriptions()`:

```python
def get_tool_definitions() -> list[dict]:
    """
    Generate Ollama-compatible tool definitions from all ready skills.
    These get passed to client.chat(tools=...) so the model can call them.
    
    Returns a list of tool dicts in the Ollama format:
    [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]
    """
    ready = get_ready_skills()
    definitions = []

    for skill in ready:
        for tool in skill.get("tools", []):
            # Build the parameters schema
            properties = {}
            required = []

            for param_name, param_info in tool.get("parameters", {}).items():
                properties[param_name] = {
                    "type": param_info.get("type", "string"),
                    "description": param_info.get("description", ""),
                }
                if param_info.get("required", False):
                    required.append(param_name)

            definition = {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }

            definitions.append(definition)

    return definitions


def get_tool_map() -> dict:
    """
    Build a lookup from tool name to execution info.
    Used by the server to execute tool calls from the model.

    Returns: {tool_name: {"executor": str, "executor_args": dict, "url_template": str|None, "skill_name": str}}
    """
    ready = get_ready_skills()
    tool_map = {}

    for skill in ready:
        for tool in skill.get("tools", []):
            tool_map[tool["name"]] = {
                "executor": tool.get("executor", ""),
                "executor_args": tool.get("executor_args", {}),
                "url_template": tool.get("url_template"),
                "skill_name": skill["name"],
            }

    return tool_map
```

### Update `_load_skill()` to preserve tools from frontmatter (line 63):

Replace:

```python
        _skills[name] = {
            "name": name,
            "description": frontmatter.get("description", ""),
            "version": frontmatter.get("version", "1.0.0"),
            "requires": frontmatter.get("requires", {}),
            "instructions": body,
            "path": str(path),
            "status": _check_requirements(frontmatter.get("requires", {})),
        }
```

With:

```python
        _skills[name] = {
            "name": name,
            "description": frontmatter.get("description", ""),
            "version": frontmatter.get("version", "1.0.0"),
            "requires": frontmatter.get("requires", {}),
            "tools": frontmatter.get("tools", []),
            "instructions": body,
            "path": str(path),
            "status": _check_requirements(frontmatter.get("requires", {})),
        }
```

### Update `list_skills()` to include tool count (line 142):

Replace:

```python
def list_skills() -> list[dict]:
    """List all skills with their status (without full instructions)."""
    return [
        {
            "name": s["name"],
            "description": s["description"],
            "version": s["version"],
            "status": s["status"],
        }
        for s in _skills.values()
    ]
```

With:

```python
def list_skills() -> list[dict]:
    """List all skills with their status (without full instructions)."""
    return [
        {
            "name": s["name"],
            "description": s["description"],
            "version": s["version"],
            "status": s["status"],
            "tool_count": len(s.get("tools", [])),
        }
        for s in _skills.values()
    ]
```

## Verification

After making all changes, run the server and check the startup output. Add this temporary print to the bottom of `init_skills()` in skills.py:

```python
    # Temporary: verify tool definitions generate correctly
    defs = get_tool_definitions()
    logger.info(f"Generated {len(defs)} tool definitions:")
    for d in defs:
        fn = d["function"]
        params = list(fn["parameters"]["properties"].keys())
        logger.info(f"  {fn['name']}: {fn['description'][:60]}... params={params}")

    tool_map = get_tool_map()
    logger.info(f"Tool map: {list(tool_map.keys())}")
```

Expected output should show:
- 3 tool definitions: web_search, web_fetch, moltbook_dashboard, moltbook_search (4 total)
- Each with correct parameters
- Tool map with all 4 tool names mapped to their executors

## What NOT to Do

- Do not modify chat.py. Tool definitions are not passed to the model yet — that's Task 29.
- Do not modify server.py. The keyword matching system stays for now — that's Task 29.
- Do not change config.py beyond the CHAT_MODEL line.
- Do not remove the old SKILL.md backup (skills/moltbook/SKILL.old) — it may be useful as reference.
- Do not add any behavioral directives to the SKILL.md instruction sections.
- Do not add formatting functions. The model will receive raw executor output.

## What This Enables

After this task, `skills.get_tool_definitions()` returns a list of Ollama-compatible tool definitions ready to pass to `client.chat(tools=...)`. And `skills.get_tool_map()` returns a lookup the server can use to dispatch tool calls to the right executor with the right arguments.

Task 29 will wire these into chat.py and server.py, replacing the keyword matching system entirely.
