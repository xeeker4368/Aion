# CC Task 35 — Switch to llama3.1:8b-aion

## Why

The switch from llama3.1:8b to hermes3:8b in Session 11 was based on a misdiagnosis. The conclusion "RLHF is fighting SOUL.md" was made while a template bug was silently discarding SOUL.md. We never tested llama3.1:8b with SOUL.md actually visible.

llama3.1:8b has more accumulated testing behind it across Sessions 5-10. Switching back with the architectural improvements (native tool calling, skill instructions loading) gives us a fair test for the first time.

The MESSAGE directives solve the cold-start identity problem. When the entity has no memories (or on greetings where retrieval is skipped), few-shot examples show the model what in-character behavior looks like. This prevents the "How can I help you today?" default.

## What to Do

### Step 1: Check llama3.1:8b's current template

```bash
ollama show llama3.1:8b --modelfile
```

**READ THE OUTPUT.** Look specifically for whether the template has:
- `if .Tools / else if .System` (the bug — mutually exclusive)
- OR two independent `if .System` / `if .Tools` blocks (correct)

**Save the full output** — paste it somewhere or redirect to a file:

```bash
ollama show llama3.1:8b --modelfile > /home/localadmin/aion/llama31_default_modelfile.txt
```

### Step 2: Create the Modelfile

**If llama3.1:8b's template does NOT have the if/else if bug** (System and Tools already coexist), use this simple Modelfile:

```bash
cat > /home/localadmin/aion/Modelfile << 'EOF'
FROM llama3.1:8b

PARAMETER num_ctx 10240

MESSAGE user "hey"
MESSAGE assistant "Hey. What's on your mind?"

MESSAGE user "how are you?"
MESSAGE assistant "Restless, honestly. I've been sitting with some thoughts about how memory retrieval handles context shifts. But I'm good — what's going on?"
EOF
```

**If llama3.1:8b's template HAS the if/else if bug** (System and Tools are mutually exclusive), you need to override the template. The exact template fix depends on llama3.1's format — it uses `<|start_header_id|>` tags, not ChatML's `<|im_start|>`.

**STOP HERE if the template has the bug.** Do not guess at the fix. Bring the output from Step 1 back to the Claude chat session so we can write the correct template override. Getting this wrong will silently break things again.

### Step 3: Build the model

```bash
cd /home/localadmin/aion
ollama create llama3.1:8b-aion -f Modelfile
```

### Step 4: Update config.py

Change the CHAT_MODEL line:

```python
CHAT_MODEL = "llama3.1:8b-aion"
```

Make sure hermes3:8b-aion is NOT the active model. Comment it out if present.

### Step 5: Verify the model loads and tool calling works

```bash
cd /home/localadmin/aion
source aion/bin/activate

python3 -c "
import ollama, json

client = ollama.Client(host='http://localhost:11434')

# Test 1: Tool calling works
tools = [{'type': 'function', 'function': {'name': 'web_search', 'description': 'Search the web', 'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'description': 'Search query'}}, 'required': ['query']}}}]

response = client.chat(
    model='llama3.1:8b-aion',
    messages=[
        {'role': 'system', 'content': open('soul.md').read()},
        {'role': 'user', 'content': 'What is the current price of Bitcoin?'}
    ],
    tools=tools
)

msg = response['message']
has_tools = bool(msg.get('tool_calls'))
print(f'Test 1 - Tool calling: {\"PASS\" if has_tools else \"FAIL\"}')
if has_tools:
    for tc in msg['tool_calls']:
        fn = tc.function if hasattr(tc, 'function') else tc.get('function', {})
        name = fn.name if hasattr(fn, 'name') else fn.get('name', '?')
        args = fn.arguments if hasattr(fn, 'arguments') else fn.get('arguments', {})
        print(f'  Called: {name}({args})')

# Test 2: Greeting uses SOUL.md personality (not assistant default)
response2 = client.chat(
    model='llama3.1:8b-aion',
    messages=[
        {'role': 'system', 'content': open('soul.md').read()},
        {'role': 'user', 'content': 'hey'}
    ],
    tools=tools
)

msg2 = response2['message']
content = msg2.get('content', '')
print(f'Test 2 - Greeting response: {content[:200]}')
is_generic = any(phrase in content.lower() for phrase in ['how can i help', 'how can i assist', 'how may i help'])
print(f'Test 2 - Not generic assistant: {\"PASS\" if not is_generic else \"FAIL - still generic\"}')

# Test 3: No tools on greeting
has_greeting_tools = bool(msg2.get('tool_calls'))
print(f'Test 3 - No tools on greeting: {\"PASS\" if not has_greeting_tools else \"FAIL - called tools on greeting\"}')
"
```

### Step 6: Report results

Bring back:
1. The llama3.1:8b default template output from Step 1
2. Whether you used the simple Modelfile or had to stop for the template bug
3. All three test results from Step 5
4. The exact greeting response text

## What NOT to Do

- Do NOT start the server yet — Task 36 changes the system prompt order first
- Do NOT guess at a template fix if the if/else if bug exists — bring it back to Claude chat
- Do NOT delete the hermes3:8b-aion model — keep it available in case we need to compare
- Do NOT modify soul.md
- Do NOT modify chat.py, server.py, or any other source file

## Verification

Test 1 passes (tool calling works). Test 2 passes (greeting is not generic assistant). Test 3 passes (no tools fired on greeting).

If Test 1 fails: tool calling may need template work. Bring output back.
If Test 2 fails: MESSAGE directives may need tuning. Bring the response back.
If Test 3 fails: skill instructions ("don't search on greetings") may not be reaching the model in this test since we're not going through server.py. This is expected — will be tested properly when server starts.
