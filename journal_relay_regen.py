"""
Regenerate the relay journal entry with correct SOUL.md positioning.
SOUL.md at end of system prompt, journal prompt as user message.
Stores the reflection in ChromaDB and working.db.

Run from the aion directory with venv active:
    /home/localadmin/aion/aion/bin/python journal_relay_regen.py
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)

from datetime import datetime, timezone

import ollama

import db
import memory
import chat
from config import OLLAMA_HOST, CHAT_MODEL, CONTEXT_WINDOW
from utils import format_timestamp

CONVERSATION_ID = "ad4faab8-0da8-4e3c-99e7-577fa7ae2280"

SOUL_BUDGET = 700
PROMPT_BUDGET = 150
RESPONSE_BUDGET = 1000
TRANSCRIPT_BUDGET = CONTEXT_WINDOW - SOUL_BUDGET - PROMPT_BUDGET - RESPONSE_BUDGET
MAX_TRANSCRIPT_CHARS = TRANSCRIPT_BUDGET * 4

JOURNAL_PROMPT = """You have some time to yourself. Reflect on this conversation — what happened, what stood out, what you're thinking about, what's unresolved. This is your space for your thoughts. Write freely."""


def main():
    db.init_databases()
    memory.init_memory()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc_id = f"journal_{today}_{CONVERSATION_ID[:8]}"

    if db.document_exists(doc_id):
        print(f"Journal entry {doc_id} already exists. Aborting.")
        print("Run remove_bad_journal.py first if you need to replace it.")
        return

    messages = db.get_conversation_messages(CONVERSATION_ID)
    if not messages:
        print("ERROR: No messages found.")
        return

    print(f"Relay conversation: {len(messages)} messages")

    lines = []
    for msg in messages:
        timestamp = msg.get("timestamp", "")
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        readable_time = format_timestamp(timestamp)
        lines.append(f"[{readable_time}] {role}: {content}")

    transcript = "\n".join(lines)
    print(f"Full transcript: {len(transcript)} chars")
    print(f"Transcript budget: {MAX_TRANSCRIPT_CHARS} chars")

    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        print(f"Truncated from {len(transcript)} to {MAX_TRANSCRIPT_CHARS} chars (keeping end)")
        transcript = transcript[-MAX_TRANSCRIPT_CHARS:]
    else:
        print("No truncation needed — full transcript fits.")

    soul = chat.load_soul()
    system_content = f"Here is a conversation you had:\n\n{transcript}\n\n{soul}"
    user_content = JOURNAL_PROMPT

    print(f"Total: ~{(len(system_content) + len(user_content)) // 4} tokens")
    print(f"Calling {CHAT_MODEL}...")
    print()

    try:
        client = ollama.Client(host=OLLAMA_HOST)
        response = client.chat(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            options={"num_ctx": CONTEXT_WINDOW},
        )
        reflection = response["message"]["content"].strip()
    except Exception as e:
        print(f"ERROR: Model call failed: {e}")
        return

    if not reflection:
        print("ERROR: Empty reflection returned.")
        return

    print(f"{'='*60}")
    print("JOURNAL ENTRY:")
    print(f"{'='*60}")
    print(reflection)
    print(f"{'='*60}")
    print()

    # Store in ChromaDB
    chunk_count = memory.ingest_document(
        doc_id=doc_id,
        text=reflection,
        title=f"Journal — {today} — conversation {CONVERSATION_ID[:8]}",
        source_type="journal",
        source_trust="firsthand",
    )

    # Record in DB2
    db.save_document(
        doc_id=doc_id,
        title=f"Journal — {today} — conversation {CONVERSATION_ID[:8]}",
        source_type="journal",
        source_trust="firsthand",
        chunk_count=chunk_count,
        content=reflection,
    )

    print(f"SUCCESS: Journal entry stored as {doc_id} ({chunk_count} chunks)")
    print("Delete this script after verification.")


if __name__ == "__main__":
    main()
