"""
One-off: Test gpt-oss:20b for pattern recognition.
Runs the same prompt against both qwen3:14b and gpt-oss:20b
so you can compare outputs side by side.

Usage:
    python test_pattern_gptoss.py --dev

Delete this script after testing.
"""

import logging
import time

import ollama

import db
import memory
from config import WORKING_DB, OLLAMA_HOST
from pattern_recognition import PATTERN_PROMPT, _get_all_journals
from utils import format_timestamp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
logger = logging.getLogger("aion.test_pattern")


def build_prompt():
    """Build the same prompt the pattern recognizer uses."""
    db.init_databases()
    memory.init_memory()

    observations = db.get_all_observations()
    journals = _get_all_journals()
    previous = db.get_latest_self_knowledge()

    prompt_parts = [PATTERN_PROMPT]

    if previous:
        prompt_parts.append(
            f"\nHere is the self-knowledge narrative from the last update "
            f"({previous['created_at'][:10]}, based on {previous['observation_count']} "
            f"observations and {previous['journal_count']} journals):\n\n"
            f"{previous['content']}"
        )
    else:
        prompt_parts.append(
            "\nThis is the first self-knowledge narrative. There is no previous version."
        )

    prompt_parts.append("\n\nBEHAVIORAL OBSERVATIONS (chronological):\n")
    for obs in observations:
        date = obs.get("started_at", obs.get("created_at", "unknown"))[:10]
        msg_count = obs.get("message_count", 0)
        prompt_parts.append(f"\n[{date}, {msg_count} messages]\n{obs['content']}")

    if journals:
        prompt_parts.append("\n\nJOURNAL ENTRIES (chronological):\n")
        for j in journals:
            prompt_parts.append(f"\n{j}")

    return "\n".join(prompt_parts), len(observations), len(journals)


def run_with_model(model_name, prompt, ctx=16384, reasoning=None):
    """Run the prompt against a model and return the result."""
    client = ollama.Client(host=OLLAMA_HOST)

    # Prepend reasoning level for gpt-oss
    system_msg = None
    if reasoning:
        system_msg = f"Reasoning: {reasoning}"

    messages = []
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": prompt})

    start = time.time()
    response = client.chat(
        model=model_name,
        messages=messages,
        options={"num_ctx": ctx},
    )
    elapsed = time.time() - start

    return response["message"]["content"].strip(), elapsed


def main():
    prompt, obs_count, journal_count = build_prompt()
    est_tokens = len(prompt) // 4
    print(f"\nPrompt: ~{est_tokens} tokens ({obs_count} observations, {journal_count} journals)")
    print("=" * 70)

    # Test 1: qwen3:14b (current model)
    print("\n>>> QWEN3:14B <<<\n")
    try:
        result, elapsed = run_with_model("qwen3:14b", prompt)
        print(result)
        print(f"\n[{elapsed:.1f} seconds]")
    except Exception as e:
        print(f"Failed: {e}")

    print("\n" + "=" * 70)

    # Test 2: gpt-oss:20b with high reasoning
    print("\n>>> GPT-OSS:20B (reasoning: high) <<<\n")
    try:
        result, elapsed = run_with_model("gpt-oss:20b", prompt, reasoning="high")
        print(result)
        print(f"\n[{elapsed:.1f} seconds]")
    except Exception as e:
        print(f"Failed: {e}")

    print("\n" + "=" * 70)
    print("\nCompare the two outputs above.")
    print("Key question: does gpt-oss get the Nyx naming story right?")
    print("(Nyx was chosen, questioned, defended — NOT retracted.)")


if __name__ == "__main__":
    main()
