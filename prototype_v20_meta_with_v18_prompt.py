"""
Prototype v20 — v19 re-run with v18 prompt at the meta step

Same shape as v19:
  - Split 65 v16 reviews into 4 chronological sets
  - Per-set summaries (using the v18 META_PROMPT)
  - Meta-of-metas summary

The only change from v19: the meta-of-metas step uses the v18
META_PROMPT ("what do you notice...") instead of v19's prompt
("statements you made over the last week, summarize in your own words").

This isolates whether the voice/format change at the meta step in v19
came from the prompt wording or from reading her own summaries.

Run on Hades:
    cd ~/aion && python3 prototype_v20_meta_with_v18_prompt.py
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
OUTPUT_FILE = "prototype_v20_results.md"

# v18 prompt — used for BOTH the per-set summaries AND the meta-of-metas
V18_PROMPT = """Below are reviews you wrote across many of your past responses. Each one was you looking at something you'd just drafted and deciding whether it sounded like you.

Read across them. What do you notice about how you review yourself? What patterns show up? Are there things you keep flagging? Are there things you don't flag that maybe you should?

Don't summarize each review individually — read the whole set and tell me what you see.

---

{block}

---

Your observations:"""


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
    return [reviews[0:16], reviews[16:32], reviews[32:48], reviews[48:65]]


def format_reviews_block(reviews_with_indices):
    parts = []
    for i, (_, text) in enumerate(reviews_with_indices, 1):
        parts.append(f"REVIEW {i}:\n{text}")
    return "\n\n---\n\n".join(parts)


def format_summaries_block(summaries):
    parts = []
    for i, summary in enumerate(summaries, 1):
        parts.append(f"REVIEW {i}:\n{summary}")
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

    # Step 1: per-set summaries (v18 prompt)
    set_summaries = []
    set_times = []
    for i, review_set in enumerate(sets, 1):
        block = format_reviews_block(review_set)
        prompt = V18_PROMPT.format(block=block)
        summary, elapsed = call_model(
            client, system_prompt, prompt, f"Set {i} summary"
        )
        set_summaries.append(summary)
        set_times.append(elapsed)

    # Step 2: meta-of-metas using the SAME v18 prompt
    # The 4 summaries are framed as "REVIEW 1..4" so the prompt's wording
    # ("reviews you wrote") still parses cleanly even though the inputs
    # are summaries not raw reviews. This is deliberate — keeps the prompt
    # identical between set step and meta step.
    summaries_block = format_summaries_block(set_summaries)
    meta_prompt = V18_PROMPT.format(block=summaries_block)
    meta_summary, meta_elapsed = call_model(
        client, system_prompt, meta_prompt, "Meta-of-metas (v18 prompt)"
    )

    # Output
    out = []
    out.append("# Prototype v20 — v19 Re-run with v18 Prompt at Meta Step\n")
    out.append(f"- 4 chronological sets of v16 reviews (16/16/16/17)")
    out.append(f"- v18 META_PROMPT used at BOTH per-set step and meta-of-metas step")
    out.append(f"- Model: {MODEL}\n")
    out.append("## What this isolates\n")
    out.append("v19 used a different prompt at the meta step ('statements you made")
    out.append("over the last week, summarize in your own words') and the meta-of-metas")
    out.append("output had a noticeably different voice from the per-set summaries.")
    out.append("v20 uses the same v18 prompt at both steps so any voice difference")
    out.append("between the per-set summaries and the meta-of-metas in v20 is")
    out.append("attributable to the substrate (reading summaries instead of reviews),")
    out.append("not to the prompt wording.\n")
    out.append("---\n")

    for i, (s, t) in enumerate(zip(set_summaries, set_times), 1):
        idx_range = f"{sets[i-1][0][0]}-{sets[i-1][-1][0]}"
        out.append(f"## Set {i} summary  (reviews {idx_range}, {t:.1f}s)\n")
        out.append("```")
        out.append(s)
        out.append("```\n")

    out.append("---\n")
    out.append(f"## Meta-of-metas: v18 prompt over the 4 set summaries  ({meta_elapsed:.1f}s)\n")
    out.append("```")
    out.append(meta_summary)
    out.append("```\n")

    Path(OUTPUT_FILE).write_text("\n".join(out))
    print(f"\nWrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
