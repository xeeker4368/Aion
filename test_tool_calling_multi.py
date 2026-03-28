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
