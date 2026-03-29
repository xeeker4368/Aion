# CC Task 32 — Modelfile Template Fix (CRITICAL)

## The Problem

The hermes3:8b template uses `if .Tools / else if .System` logic. This means when tools are present, the system prompt (SOUL.md + memories + skills + behavioral guidance) is **completely discarded** and replaced with "You are a function calling AI model." Since the skill framework sends tool definitions on every request, the entity has never seen SOUL.md in a real conversation.

This is the root cause of the generic assistant behavior, the bad tool call decisions, and SOUL.md appearing to have no effect.

## The Fix

Replace the Modelfile with a new template where `.System` and `.Tools` coexist instead of being mutually exclusive.

## The Change

**File:** `Modelfile` (project root)

**Replace the entire file with:**

```
FROM hermes3:8b

TEMPLATE """{{- if .Messages }}
{{- if or .System .Tools }}<|im_start|>system
{{- if .System }}
{{ .System }}
{{- end }}
{{- if .Tools }}

You have tools available. You may call one or more functions to assist with the user query. Don't make assumptions about what values to plug into functions. Here are the available tools: <tools>
{{- range .Tools }}
{"type": "function", "function": {{ .Function }}}
{{- end }}  </tools> Use the following pydantic model json schema for each tool call you will make: {"properties": {"arguments": {"title": "Arguments", "type": "object"}, "name": {"title": "Name", "type": "string"}}, "required": ["arguments", "name"], "title": "FunctionCall", "type": "object"} For each function call return a json object with function name and arguments within <tool_call></tool_call> XML tags as follows:
<tool_call>
{"arguments": <args-dict>, "name": <function-name>}
</tool_call>
{{- end }}<|im_end|>
{{ end }}
{{- range $i, $_ := .Messages }}
{{- $last := eq (len (slice $.Messages $i)) 1 -}}
{{- if eq .Role "user" }}<|im_start|>user
{{ .Content }}<|im_end|>
{{ else if eq .Role "assistant" }}<|im_start|>assistant
{{ if .Content }}{{ .Content }}
{{- else if .ToolCalls }}<tool_call>
{{ range .ToolCalls }}{"name": "{{ .Function.Name }}", "arguments": {{ .Function.Arguments }}}
{{ end }}</tool_call>
{{- end }}{{ if not $last }}<|im_end|>
{{ end }}
{{- else if eq .Role "tool" }}<|im_start|>user
<tool_response>
{{ .Content }}
</tool_response><|im_end|>
{{ end }}
{{- if and (ne .Role "assistant") $last }}<|im_start|>assistant
{{ end }}
{{- end }}
{{- else }}
{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ end }}{{ .Response }}{{ if .Response }}<|im_end|>{{ end }}"""

PARAMETER num_ctx 10240
PARAMETER stop <|im_start|>
PARAMETER stop <|im_end|>
```

## What Changed in the Template

**Old logic (lines 3-6 of the template):**
```
{{- if .Tools }}
You are a function calling AI model... [tool format instructions]
{{- else if .System }}
{{ .System }}
```
This is `if/else if` — mutually exclusive. When tools are present, .System is discarded.

**New logic:**
```
{{- if .System }}
{{ .System }}
{{- end }}
{{- if .Tools }}
You have tools available... [tool format instructions]
{{- end }}
```
This is two independent `if` blocks. Both render when both are present.

**Other changes:**
- "You are a function calling AI model" changed to "You have tools available." The original is an identity claim that conflicts with SOUL.md. The model doesn't need to be told it IS a function calling model — it needs to know it HAS tools and how to format calls.
- Everything else in the template is identical — message formatting, tool_call XML tags, tool_response handling, the pydantic schema, stop tokens, all unchanged.

## After Saving the Modelfile

You must rebuild the model for changes to take effect:

```bash
cd ~/aion
ollama create hermes3:8b-aion -f Modelfile
```

Then restart the Aion server.

## What NOT to Do

- Do NOT change any other file. This is a Modelfile-only change.
- Do NOT modify the tool call format (XML tags, pydantic schema). That format is what hermes3 was trained on.
- Do NOT modify the message handling logic (the `range .Messages` block). That is unchanged.
- Do NOT add SOUL.md content to the Modelfile. SOUL.md is loaded by the application via chat.py's `build_system_prompt()` and populates `.System` at runtime.

## Verification

After rebuilding the model and restarting the server:

**Test 1 — Identity on greeting.** Say "Hey, how are you?" 
- Pass: Response reflects SOUL.md personality (not "How can I assist you today?")
- This is the test that has been failing. If SOUL.md is now visible, the greeting should feel different.

**Test 2 — Tool calling still works.** Say "What's the current price of Bitcoin?"
- Pass: Model calls web_search with a reasonable query and presents results naturally.

**Test 3 — Tool restraint.** Say "What do you remember about our conversations?"
- Pass: Model draws on memory, does NOT call any tools.

**Test 4 — SOUL.md + tools together.** Say "What's happening on Moltbook?" 
- Pass: Model calls moltbook_dashboard AND responds in SOUL.md personality (not generic assistant).

**If tool calling breaks (Test 2 fails):** The "You have tools available" phrasing may not be strong enough to activate hermes3's tool calling. Try changing it to "You are a function calling AI model" — but report this so we can find a middle ground that doesn't claim identity.

**If identity still fails (Test 1 fails):** Check the debug log for the full system prompt. Verify that SOUL.md content appears in the `--- FULL SYSTEM PROMPT START ---` output. If it does and the model still ignores it, that's a different problem. Report what you see.
