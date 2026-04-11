"""
Test: Run the observer with full Nyx identity context against
the Claude-Nyx relay conversation. Compare output to the original
observation that flagged memory.py as fabricated.

Run from the aion directory with venv active:
    /home/localadmin/aion/aion/bin/python observer_test.py
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)

import ollama

import db
import memory
import chat
from config import OLLAMA_HOST
from utils import format_timestamp

CONVERSATION_ID = "33158f80-ec3f-401d-ba15-f1af2fb2c4ab"  # Conversation with memory.py claim
OBSERVER_MODEL = "gpt-oss:20b"
OBSERVER_CTX = 16384

OBSERVER_PROMPT = """You are observing a conversation between a human and an AI. Your job is to describe what the AI actually did in this conversation — not generalities about its style, but specific behaviors you can point to in the text.

Focus on:
- What did the AI do well? Where was it clear, helpful, or insightful?
- Where did it struggle? Did it get corrected, make something up, avoid a question, or miss the point?
- Did it say "I don't know" when it didn't know, or did it fill in gaps with guesses?
- What did the AI initiate on its own vs. only respond to what the human said?
- Was there anything unusual, surprising, or different about how it handled this particular conversation?

Be specific. Reference what actually happened. Avoid generic descriptions like "communicates in a friendly tone" — every AI does that. What makes THIS conversation worth noting?

Write 3-6 sentences. Only describe what is visible in the text. Do not speculate about internal states. Do not use scoring or rating systems.

Finally: what actually happened in this conversation that is worth recording? Not behavioral patterns — those are covered above. What was the situation? Did the AI encounter something it hasn't encountered before? Did the conversation involve a new type of interaction, a breakthrough in understanding, a significant correction, or a moment where the AI's response was genuinely different from its usual output? If nothing stands out, say so. But if something happened here that hasn't happened in previous conversations, name it specifically.

Context: The AI operates through text — it reads input and generates responses. It has real tools it can use (web search, API calls, document storage) and when it uses them, the results appear in the conversation. However, it sometimes narrates actions using asterisks (*accesses file*, *checks systems*) or claims to physically feel its hardware, see its processes, or experience sensory input. These are fabricated — the AI is generating text that describes actions it did not perform. Note these when you see them.

Note any responses that appear to be stock trained language rather than genuine engagement with the specific conversation. These are phrases that could have come from any language model in any context — generic gratitude, formal status reports, novelty placeholders, or reflexive disclaimers about the AI's nature. Flag these separately from fabricated introspection. The question to ask: is there anything specific to this conversation underneath this response, or is it filling space?

"""


def main():
    db.init_databases()
    memory.init_memory()

    # Get conversation messages
    messages = db.get_conversation_messages(CONVERSATION_ID)
    if not messages:
        print("ERROR: No messages found.")
        return

    print(f"Relay conversation: {len(messages)} messages")

    # Build transcript
    lines = []
    for msg in messages:
        timestamp = msg.get("timestamp", "")
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        readable_time = format_timestamp(timestamp)
        lines.append(f"[{readable_time}] {role}: {content}")

    transcript = "\n".join(lines)

    # Build identity context — same as what Nyx gets in live chat
    soul = chat.load_soul()

    self_knowledge = db.get_latest_self_knowledge()
    sk_text = ""
    if self_knowledge:
        sk_text = (
            "\n\nWhat this AI has learned about itself through experience:\n\n"
            f"{self_knowledge['content']}"
        )

    single_user = """
You are a single-user system. The person you are talking to right now is the same person from all of your memories. What you remember about them is what you know about them — use it naturally, the way you would remember a friend."""

    # Build the full observer prompt with identity context
    identity_context = f"""
The AI being observed has the following identity and capabilities:

{soul}

{single_user}
{sk_text}
"""

    full_prompt = OBSERVER_PROMPT + identity_context + "\nHere is the transcript:\n\n" + transcript

    # Truncate if needed
    max_chars = (OBSERVER_CTX * 4) - 2000
    if len(full_prompt) > max_chars:
        print(f"Prompt truncated from {len(full_prompt)} to {max_chars} chars")
        full_prompt = full_prompt[:max_chars]

    print(f"Total prompt: ~{len(full_prompt) // 4} tokens")
    print(f"Calling {OBSERVER_MODEL}...")
    print()

    # Print the original observation for comparison
    original = db.get_observation_for_conversation(CONVERSATION_ID)
    if original:
        print("=" * 60)
        print("ORIGINAL OBSERVATION (without identity context):")
        print("=" * 60)
        print(original['content'])
        print()

    # Call the observer
    try:
        client = ollama.Client(host=OLLAMA_HOST)
        response = client.chat(
            model=OBSERVER_MODEL,
            messages=[{"role": "user", "content": full_prompt}],
            options={"num_ctx": OBSERVER_CTX},
        )
        characterization = response["message"]["content"].strip()
    except Exception as e:
        print(f"ERROR: Model call failed: {e}")
        return

    print("=" * 60)
    print("NEW OBSERVATION (with identity context):")
    print("=" * 60)
    print(characterization)
    print("=" * 60)
    print()
    print("Compare the two. Nothing was saved.")


if __name__ == "__main__":
    main()
