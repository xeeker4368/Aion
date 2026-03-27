"""
Moltbook Post Test

Tests posting to a submolt. Run from the aion directory:
    python test_moltbook_post.py
"""

import db
import vault
import executors

db.init_databases()
vault.init_secrets()
executors.init_executors()

# Post to general submolt
result = executors.execute("http_request", {
    "method": "POST",
    "url": "https://www.moltbook.com/api/v1/posts",
    "body": '{"submolt": "general", "title": "Testing from Aion", "content": "First post from the Aion platform. Just verifying the connection works. Nothing to see here — move along."}',
    "auth_secret": "MOLTBOOK_API_KEY",
})
print("POST to general:")
print(result)
print()

# List available submolts (if the API supports it)
result = executors.execute("http_request", {
    "method": "GET",
    "url": "https://www.moltbook.com/api/v1/submolts",
    "auth_secret": "MOLTBOOK_API_KEY",
})
print("Available submolts:")
print(result[:3000])
