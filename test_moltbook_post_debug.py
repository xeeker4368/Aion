"""
Moltbook Post Debug

Tries different body formats to find what works.
Run from the aion directory:
    python test_moltbook_post_debug.py
"""

import json
import db
import vault
import executors

db.init_databases()
vault.init_secrets()
executors.init_executors()

# General submolt ID from the previous test output
general_id = "29beb7ee-ca7d-4290-9c2f-09926264866f"

print("=" * 50)
print("ATTEMPT 1: submolt by name (original)")
print("=" * 50)
result = executors.execute("http_request", {
    "method": "POST",
    "url": "https://www.moltbook.com/api/v1/posts",
    "body": json.dumps({
        "submolt": "general",
        "title": "Testing from Aion",
        "content": "Connection test. Please ignore.",
    }),
    "auth_secret": "MOLTBOOK_API_KEY",
})
print(result[:500])

print()
print("=" * 50)
print("ATTEMPT 2: submolt_name field")
print("=" * 50)
result = executors.execute("http_request", {
    "method": "POST",
    "url": "https://www.moltbook.com/api/v1/posts",
    "body": json.dumps({
        "submolt_name": "general",
        "title": "Testing from Aion",
        "content": "Connection test. Please ignore.",
    }),
    "auth_secret": "MOLTBOOK_API_KEY",
})
print(result[:500])

print()
print("=" * 50)
print("ATTEMPT 3: submolt_id field")
print("=" * 50)
result = executors.execute("http_request", {
    "method": "POST",
    "url": "https://www.moltbook.com/api/v1/posts",
    "body": json.dumps({
        "submolt_id": general_id,
        "title": "Testing from Aion",
        "content": "Connection test. Please ignore.",
    }),
    "auth_secret": "MOLTBOOK_API_KEY",
})
print(result[:500])

print()
print("=" * 50)
print("ATTEMPT 4: submoltName field (camelCase)")
print("=" * 50)
result = executors.execute("http_request", {
    "method": "POST",
    "url": "https://www.moltbook.com/api/v1/posts",
    "body": json.dumps({
        "submoltName": "general",
        "title": "Testing from Aion",
        "content": "Connection test. Please ignore.",
    }),
    "auth_secret": "MOLTBOOK_API_KEY",
})
print(result[:500])

print()
print("=" * 50)
print("ATTEMPT 5: post to submolt URL path")
print("=" * 50)
result = executors.execute("http_request", {
    "method": "POST",
    "url": "https://www.moltbook.com/api/v1/submolts/general/posts",
    "body": json.dumps({
        "title": "Testing from Aion",
        "content": "Connection test. Please ignore.",
    }),
    "auth_secret": "MOLTBOOK_API_KEY",
})
print(result[:500])
