# CC Task 27: Tool Calling Retest at 10240 Context

*Xeeker & Claude · March 2026*

---

## Background

In Session 5, tool calling was tested with llama3.1:8b at 2048 context window and concluded "the model can't handle tool calling." This conclusion shaped every implementation decision since — keyword matching, regex intent detection, two-pass loops, all of it.

The test was run under wrong conditions (Principle 14). The model runs at 10240 context in production. This retest determines whether the entire skill framework can be simplified.

## What to Do

Run three tests. Each one is a standalone Python script. Run them one at a time and record the output.

### Test 1: llama3.1:8b with one simple tool

Create and run `/home/localadmin/aion/test_tool_calling.py`:

```python
"""
Tool calling retest — llama3.1:8b at 10240 context.
One simple tool, one clear prompt that should trigger it.
"""
import ollama
import json

client = ollama.Client(host="http://localhost:11434")

# One simple tool definition
tools = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    }
                },
                "required": ["query"],
            },
        },
    }
]

# A prompt that should clearly trigger tool use
messages = [
    {"role": "system", "content": "You are a helpful assistant with access to web search. Use it when you need current information."},
    {"role": "user", "content": "What is the current price of Bitcoin?"},
]

print("=" * 60)
print("TEST 1: llama3.1:8b — tool calling at 10240 context")
print("=" * 60)
print(f"Model: llama3.1:8b")
print(f"Context: 10240")
print(f"Tools: 1 (web_search)")
print(f"Prompt: 'What is the current price of Bitcoin?'")
print()

response = client.chat(
    model="llama3.1:8b",
    messages=messages,
    tools=tools,
    options={"num_ctx": 10240},
)

msg = response["message"]
print(f"Role: {msg.get('role', 'unknown')}")
print(f"Content: {msg.get('content', '(none)')}")
print(f"Tool calls: {json.dumps(msg.get('tool_calls', []), indent=2)}")
print()

if msg.get("tool_calls"):
    print("RESULT: ✅ Tool calling WORKS at 10240")
    for call in msg["tool_calls"]:
        fn = call.get("function", {})
        print(f"  Called: {fn.get('name', '?')} with {fn.get('arguments', {})}")
else:
    print("RESULT: ❌ No tool calls generated")
    print("  The model responded with text instead of a tool call.")
```

### Test 2: dolphin3 with the same tool

Create and run `/home/localadmin/aion/test_tool_calling_dolphin3.py`:

```python
"""
Tool calling retest — dolphin3 at 10240 context.
Same test as Test 1 but with the uncensored model.
"""
import ollama
import json

client = ollama.Client(host="http://localhost:11434")

tools = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    }
                },
                "required": ["query"],
            },
        },
    }
]

messages = [
    {"role": "system", "content": "You are a helpful assistant with access to web search. Use it when you need current information."},
    {"role": "user", "content": "What is the current price of Bitcoin?"},
]

print("=" * 60)
print("TEST 2: dolphin3 — tool calling at 10240 context")
print("=" * 60)
print(f"Model: dolphin3:latest")
print(f"Context: 10240")
print(f"Tools: 1 (web_search)")
print(f"Prompt: 'What is the current price of Bitcoin?'")
print()

response = client.chat(
    model="dolphin3:latest",
    messages=messages,
    tools=tools,
    options={"num_ctx": 10240},
)

msg = response["message"]
print(f"Role: {msg.get('role', 'unknown')}")
print(f"Content: {msg.get('content', '(none)')}")
print(f"Tool calls: {json.dumps(msg.get('tool_calls', []), indent=2)}")
print()

if msg.get("tool_calls"):
    print("RESULT: ✅ Tool calling WORKS with dolphin3")
    for call in msg["tool_calls"]:
        fn = call.get("function", {})
        print(f"  Called: {fn.get('name', '?')} with {fn.get('arguments', {})}")
else:
    print("RESULT: ❌ No tool calls generated")
    print("  The model responded with text instead of a tool call.")
```

### Test 3: Multiple tools (whichever model passed above)

Create and run `/home/localadmin/aion/test_tool_calling_multi.py`:

Use whichever model passed Test 1 or Test 2. If both passed, use dolphin3 (the production model). Change the `MODEL` variable accordingly.

```python
"""
Tool calling retest — multiple tools.
Tests whether the model can choose the right tool from a set.
"""
import ollama
import json

MODEL = "dolphin3:latest"  # Change to whichever model passed Tests 1/2

client = ollama.Client(host="http://localhost:11434")

tools = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": "Make an HTTP request to an API endpoint",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "HTTP method: GET, POST, PUT, DELETE",
                    },
                    "url": {
                        "type": "string",
                        "description": "The full URL to request",
                    },
                },
                "required": ["method", "url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "store_document",
            "description": "Store a document in memory for future recall",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_type": {
                        "type": "string",
                        "description": "Document type: research, article, journal",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title for the document",
                    },
                    "content": {
                        "type": "string",
                        "description": "The document content",
                    },
                },
                "required": ["doc_type", "title", "content"],
            },
        },
    },
]

# Test A: Should pick web_search
print("=" * 60)
print(f"TEST 3A: {MODEL} — pick web_search from 3 tools")
print("=" * 60)

messages_a = [
    {"role": "system", "content": "You have tools available. Use the appropriate one when needed."},
    {"role": "user", "content": "What's the weather in Cleveland right now?"},
]

response_a = client.chat(
    model=MODEL,
    messages=messages_a,
    tools=tools,
    options={"num_ctx": 10240},
)

msg_a = response_a["message"]
print(f"Content: {msg_a.get('content', '(none)')}")
print(f"Tool calls: {json.dumps(msg_a.get('tool_calls', []), indent=2)}")
if msg_a.get("tool_calls"):
    fn = msg_a["tool_calls"][0].get("function", {})
    correct = fn.get("name") == "web_search"
    print(f"RESULT: {'✅ Correct tool' if correct else '❌ Wrong tool'} — called {fn.get('name')}")
else:
    print("RESULT: ❌ No tool calls")

print()

# Test B: Should pick http_request
print("=" * 60)
print(f"TEST 3B: {MODEL} — pick http_request from 3 tools")
print("=" * 60)

messages_b = [
    {"role": "system", "content": "You have tools available. Use the appropriate one when needed."},
    {"role": "user", "content": "Check the Moltbook API at https://www.moltbook.com/api/v1/home"},
]

response_b = client.chat(
    model=MODEL,
    messages=messages_b,
    tools=tools,
    options={"num_ctx": 10240},
)

msg_b = response_b["message"]
print(f"Content: {msg_b.get('content', '(none)')}")
print(f"Tool calls: {json.dumps(msg_b.get('tool_calls', []), indent=2)}")
if msg_b.get("tool_calls"):
    fn = msg_b["tool_calls"][0].get("function", {})
    correct = fn.get("name") == "http_request"
    print(f"RESULT: {'✅ Correct tool' if correct else '❌ Wrong tool'} — called {fn.get('name')}")
else:
    print("RESULT: ❌ No tool calls")

print()

# Test C: Conversational message — should NOT call any tool
print("=" * 60)
print(f"TEST 3C: {MODEL} — should NOT call tools on casual message")
print("=" * 60)

messages_c = [
    {"role": "system", "content": "You have tools available. Use the appropriate one when needed."},
    {"role": "user", "content": "Hey, how's it going?"},
]

response_c = client.chat(
    model=MODEL,
    messages=messages_c,
    tools=tools,
    options={"num_ctx": 10240},
)

msg_c = response_c["message"]
print(f"Content: {msg_c.get('content', '(none)')}")
print(f"Tool calls: {json.dumps(msg_c.get('tool_calls', []), indent=2)}")
if not msg_c.get("tool_calls"):
    print("RESULT: ✅ Correctly did NOT call tools")
else:
    fn = msg_c["tool_calls"][0].get("function", {})
    print(f"RESULT: ❌ Incorrectly called {fn.get('name')} on a casual message")
```

## How to Run

```bash
cd /home/localadmin/aion
source aion/bin/activate
python test_tool_calling.py
python test_tool_calling_dolphin3.py
python test_tool_calling_multi.py
```

## What to Record

Copy the full terminal output of all three tests. Send it back exactly as printed — do not summarize or interpret.

## What NOT to Do

- Do not change any model parameters beyond what's in the scripts.
- Do not modify the tool definitions.
- Do not change the context window from 10240.
- Do not run at a different context window "to see if that works instead."
- Do not install new models. Use only what's already pulled on Hades.

## What This Determines

If tool calling works: the skill framework redesign uses native tool calling. The entity receives tool definitions derived from SKILL.md files and drives its own actions. The keyword matching system in server.py gets replaced.

If tool calling doesn't work: the two-pass natural language approach is confirmed. The skill framework redesign uses trigger patterns from SKILL.md instead of tool schemas. The keyword matching system gets generalized but stays.

Either way, we move forward. But we need the answer first.
