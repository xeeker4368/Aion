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
