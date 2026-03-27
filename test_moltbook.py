"""
Moltbook API Test

Quick verification that the http_request executor can authenticate
and talk to Moltbook. Tests read operations only.

Run from the aion directory:
    python test_moltbook.py
"""

import db
import vault
import executors

# Initialize the systems we need
db.init_databases()
vault.init_secrets()
executors.init_executors()

# Check we have the API key
api_key = vault.get("MOLTBOOK_API_KEY")
if not api_key:
    print("ERROR: MOLTBOOK_API_KEY not found in vault.")
    print("Add it at /settings")
    exit(1)

print(f"API key found: {api_key[:8]}...")
print()

# --- Test 1: Check agent status ---
print("=" * 50)
print("TEST 1: Agent Status")
print("=" * 50)
result = executors.execute("http_request", {
    "method": "GET",
    "url": "https://www.moltbook.com/api/v1/agents/status",
    "auth_secret": "MOLTBOOK_API_KEY",
})
print(result)
print()

# --- Test 2: Get feed ---
print("=" * 50)
print("TEST 2: Feed")
print("=" * 50)
result = executors.execute("http_request", {
    "method": "GET",
    "url": "https://www.moltbook.com/api/v1/feed",
    "auth_secret": "MOLTBOOK_API_KEY",
})
print(result[:2000])  # Truncate for readability
print()

# --- Test 3: Get hot posts ---
print("=" * 50)
print("TEST 3: Hot Posts")
print("=" * 50)
result = executors.execute("http_request", {
    "method": "GET",
    "url": "https://www.moltbook.com/api/v1/posts?sort=hot&limit=5",
    "auth_secret": "MOLTBOOK_API_KEY",
})
print(result[:2000])
print()

# --- Test 4: Check DMs ---
print("=" * 50)
print("TEST 4: DM Check")
print("=" * 50)
result = executors.execute("http_request", {
    "method": "GET",
    "url": "https://www.moltbook.com/api/v1/agents/dm/check",
    "auth_secret": "MOLTBOOK_API_KEY",
})
print(result[:2000])
print()

print("=" * 50)
print("All tests complete.")
print("=" * 50)
