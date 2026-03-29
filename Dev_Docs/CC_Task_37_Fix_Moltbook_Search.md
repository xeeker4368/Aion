# CC Task 37 — Fix moltbook_search Executor Bug

## Why

When the entity calls `moltbook_search`, the `query` parameter is correctly interpolated into the `url_template` to build the URL. But then the same `query` parameter is ALSO added to `executor_args` by the merge loop on line 349. This means `query` gets passed as a keyword argument to `_http_request()`, which doesn't accept it. Error: `_http_request() got an unexpected keyword argument 'query'`.

## What to Change

**File:** `server.py`

**Function:** `_execute_tool_call` (around line 322)

**Current code (lines 339-355):**

```python
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
```

**Replace with:**

```python
    # Handle URL templates (e.g., moltbook search)
    consumed_params = set()
    if url_template and "query" in arguments:
        import urllib.parse
        encoded_query = urllib.parse.quote(arguments["query"])
        executor_args["url"] = url_template.replace("{query}", encoded_query)
        consumed_params.add("query")
    elif "url" in executor_args:
        # Fixed URL (e.g., moltbook dashboard) — already in executor_args
        pass

    # Merge model-provided arguments, skipping params already consumed by url_template
    for key, value in arguments.items():
        if key not in executor_args and key not in consumed_params:
            executor_args[key] = value
```

## What NOT to Do

- Do NOT modify any other function in server.py
- Do NOT modify the executor itself (`_http_request` in executors.py)
- Do NOT modify the SKILL.md files
- Do NOT change how tool definitions are generated

## Verification

```bash
cd /home/localadmin/aion
source aion/bin/activate

python3 -c "
# Simulate what _execute_tool_call does for moltbook_search
import urllib.parse

# These come from the SKILL.md tool definition
executor_args = {
    'method': 'GET',
    'auth_secret': 'MOLTBOOK_API_KEY',
    'max_chars': 8000,
}
url_template = 'https://www.moltbook.com/api/v1/search?q={query}&type=posts&limit=10'

# These come from the model's tool call
arguments = {'query': 'memory systems'}

# The fix
consumed_params = set()
if url_template and 'query' in arguments:
    encoded_query = urllib.parse.quote(arguments['query'])
    executor_args['url'] = url_template.replace('{query}', encoded_query)
    consumed_params.add('query')

for key, value in arguments.items():
    if key not in executor_args and key not in consumed_params:
        executor_args[key] = value

print(f'executor_args keys: {list(executor_args.keys())}')
print(f'url: {executor_args[\"url\"]}')
assert 'query' not in executor_args, 'FAIL: query still in executor_args'
assert 'memory%20systems' in executor_args['url'], 'FAIL: query not in URL'
print('PASS: query consumed by url_template, not passed to executor')
"
```

**Expected output:**
```
executor_args keys: ['method', 'auth_secret', 'max_chars', 'url']
url: https://www.moltbook.com/api/v1/search?q=memory%20systems&type=posts&limit=10
PASS: query consumed by url_template, not passed to executor
```

Then test with the live server by asking the entity to search Moltbook for a topic. The tool call should execute without the `unexpected keyword argument` error. (Moltbook's server may still return HTTP 500 — that's their issue, not ours.)
