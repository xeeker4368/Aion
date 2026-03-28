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
