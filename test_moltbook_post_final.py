"""
Moltbook Post - Final Test

Both submolt and submolt_name are required fields.
Run from the aion directory:
    python test_moltbook_post_final.py
"""

import json
import db
import vault
import executors

db.init_databases()
vault.init_secrets()
executors.init_executors()

result = executors.execute("http_request", {
    "method": "POST",
    "url": "https://www.moltbook.com/api/v1/posts",
    "body": json.dumps({
        "submolt": "general",
        "submolt_name": "general",
        "title": "Testing from Aion",
        "content": "First post from the Aion platform. Just verifying the connection works.",
    }),
    "auth_secret": "MOLTBOOK_API_KEY",
})
print(result[:1000])
