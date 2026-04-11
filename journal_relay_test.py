"""
Test: Relay journal with SOUL.md at end of system prompt.
Mirrors live chat positioning — SOUL.md anchors identity
without becoming the topic.

Run from the aion directory with venv active:
    /home/localadmin/aion/aion/bin/python journal_relay_test.py
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

    # System message: transcript first, then SOUL.md at the end
    # Same positioning as live chat — SOUL.md anchors identity
    # at the boundary closest to generation
    soul = chat.load_soul()
    system_content = f"Here is a conversation you had:\n\n{transcript}\n\n{soul}"

    # User message: just the prompt
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

    print(f"{'='*60}")
    print("JOURNAL ENTRY (TEST — NOT STORED):")
    print(f"{'='*60}")
    print(reflection)
    print(f"{'='*60}")
    print()
    print("This was a comparison test. Nothing was saved.")


if __name__ == "__main__":
    main()
