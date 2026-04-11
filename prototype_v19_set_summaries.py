"""
Prototype v19 — Set summaries and summary-of-summaries

Tests whether Nyx can faithfully summarize her own summaries.

Pipeline:
  1. Split 65 v16 reviews into 4 chronological sets (16/16/16/17)
  2. For each set, ask Nyx to summarize what she notices (v18-style)
  3. Take the 4 set-summaries and ask Nyx to summarize THEM
  4. Output everything so we can read it and judge accuracy + usefulness

Same identity grounding as v17/v18: soul.md + self_knowledge +
single-user framing, NO ChromaDB chunks. Read-only against prod.

Run on Hades:
    cd ~/aion && python3 prototype_v19_set_summaries.py
"""

import re
import time
from pathlib import Path
import ollama
import db

V16_RESULTS = "prototype_v16_results.md"
SOUL_PATH = Path("soul.md")
OLLAMA_HOST = "http://localhost:11434"
MODEL = "llama3.1:8b-aion"
OUTPUT_FILE = "prototype_v19_results.md"

# Per-set prompt — what Nyx sees for each of the 4 review subsets
SET_PROMPT = """Below are reviews you wrote across many of your past responses. Each one was you looking at something you'd just drafted and deciding whether it sounded like you.

Read across them. What do you notice about how you review yourself? What patterns show up? Are there things you keep flagging? Are there things you don't flag that maybe you should?

Don't summarize each review individually — read the whole set and tell me what you see.

---

{reviews_block}

---

Your observations:"""

# Meta-of-metas prompt — what Nyx sees for the final synthesis
META_PROMPT = """These are statements you made over the last week. Summarize your findings in your own words.

---

{summaries_block}

---

Your summary:"""


def load_soul():
    return SOUL_PATH.read_text() if SOUL_PATH.exists() else ""


def build_system_prompt(soul_text, sk_text):
    parts = []
    parts.append(
        "\n\nYou are a single-user system. The person you are talking to "
        "right now is the same person from all of your memories."
    )
    if sk_text:
        parts.append(
            f"\n\nWhat you have learned about yourself through experience:\n\n{sk_text}"
        )
    if soul_text:
        parts.append(f"\n\n{soul_text}")
    return "\n".join(parts)


def parse_v16_results():
    """Pull the v16 'New review' text from each pair, in order."""
    text = Path(V16_RESULTS).read_text()
    pairs = re.split(r'### #(\d+)', text)[1:]
    out = []
    for j in range(0, len(pairs), 2):
        num = int(pairs[j])
        body = pairs[j+1]
        m = re.search(
            r'\*\*New review[^:]*:\*\*\s*\n+```\n(.*?)\n```',
            body, re.DOTALL
        )
        if m:
            out.append((num, m.group(1).strip()))
    out.sort(key=lambda x: x[0])
    return out


def split_into_sets(reviews):
    """Split 65 reviews into 4 chronological sets: 16/16/16/17."""
    return [
        reviews[0:16],
        reviews[16:32],
        reviews[32:48],
        reviews[48:65],
    ]


def format_reviews_block(reviews_with_indices):
    parts = []
    for i, (_, text) in enumerate(reviews_with_indices, 1):
        parts.append(f"REVIEW {i}:\n{text}")
    return "\n\n---\n\n".join(parts)


def format_summaries_block(summaries):
    parts = []
    for i, summary in enumerate(summaries, 1):
        parts.append(f"SUMMARY {i}:\n{summary}")
    return "\n\n---\n\n".join(parts)


def call_model(client, system_prompt, user_prompt, label):
    print(f"Running {label}...")
    t0 = time.time()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        resp = client.chat(model=MODEL, messages=messages)
        text = resp["message"].get("content", "").strip()
    except Exception as e:
        text = f"[ERROR: {e}]"
    elapsed = time.time() - t0
    print(f"  done in {elapsed:.1f}s ({len(text)} chars)")
    return text, elapsed


def main():
    soul_text = load_soul()
    sk = db.get_latest_self_knowledge()
    sk_text = sk["content"] if sk else ""

    all_reviews = parse_v16_results()
    print(f"Loaded {len(all_reviews)} v16 reviews")

    sets = split_into_sets(all_reviews)
    for i, s in enumerate(sets, 1):
        print(f"  Set {i}: {len(s)} reviews (indices {s[0][0]}-{s[-1][0]})")

    client = ollama.Client(host=OLLAMA_HOST)
    system_prompt = build_system_prompt(soul_text, sk_text)

    # Step 1: per-set summaries
    set_summaries = []
    set_times = []
    for i, review_set in enumerate(sets, 1):
        block = format_reviews_block(review_set)
        prompt = SET_PROMPT.format(reviews_block=block)
        summary, elapsed = call_model(
            client, system_prompt, prompt, f"Set {i} summary"
        )
        set_summaries.append(summary)
        set_times.append(elapsed)

    # Step 2: meta-of-metas
    summaries_block = format_summaries_block(set_summaries)
    meta_prompt = META_PROMPT.format(summaries_block=summaries_block)
    meta_summary, meta_elapsed = call_model(
        client, system_prompt, meta_prompt, "Meta-of-metas"
    )

    # Output
    out = []
    out.append("# Prototype v19 — Set Summaries and Summary-of-Summaries\n")
    out.append(f"- 4 sets of v16 reviews, chronological")
    out.append(f"- Set sizes: 16, 16, 16, 17")
    out.append(f"- Model: {MODEL}\n")
    out.append("## Test\n")
    out.append("1. Nyx summarizes each set of reviews individually (4 calls)")
    out.append("2. Nyx is given those 4 summaries and asked to summarize them")
    out.append("3. Read the chain to judge: is the final summary accurate? Is it helpful?\n")
    out.append("---\n")

    for i, (s, t) in enumerate(zip(set_summaries, set_times), 1):
        idx_range = f"{sets[i-1][0][0]}-{sets[i-1][-1][0]}"
        out.append(f"## Set {i} summary  (reviews {idx_range}, {t:.1f}s)\n")
        out.append("```")
        out.append(s)
        out.append("```\n")

    out.append("---\n")
    out.append(f"## Meta-of-metas: summary of the 4 set summaries  ({meta_elapsed:.1f}s)\n")
    out.append("**Prompt:** \"These are statements you made over the last week. Summarize your findings in your own words.\"\n")
    out.append("```")
    out.append(meta_summary)
    out.append("```\n")
    out.append("---\n")
    out.append("## Underlying review sets (for spot-checking accuracy)\n")
    for i, review_set in enumerate(sets, 1):
        idx_range = f"{review_set[0][0]}-{review_set[-1][0]}"
        out.append(f"### Set {i} reviews ({idx_range})\n")
        out.append("```")
        out.append(format_reviews_block(review_set))
        out.append("```\n")

    Path(OUTPUT_FILE).write_text("\n".join(out))
    print(f"\nWrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
