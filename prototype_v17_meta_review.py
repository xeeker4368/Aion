"""
Prototype v17 — Meta-review

Takes 25 review samples (same indices for both runs) and asks Nyx to
read across them and characterize what she notices about her own
self-critique patterns.

Run 1: 25 ORIGINAL reviews from prod (with template propagation)
Run 2: 25 v16 reviews (no chunks in review context)

Same 25 indices in both runs. Same prompt. Same identity grounding
(soul.md + self_knowledge + single-user framing, NO ChromaDB chunks).
The only variable is which set of reviews she's reading.

Output: prototype_v17_results.md

Run on Hades:
    cd ~/aion && python3 prototype_v17_meta_review.py
"""

import sqlite3
import re
import time
from pathlib import Path
import ollama
import db

PROD_DB = "data/prod/working.db"
V16_RESULTS = "prototype_v16_results.md"
SOUL_PATH = Path("soul.md")
OLLAMA_HOST = "http://localhost:11434"
MODEL = "llama3.1:8b-aion"
OUTPUT_FILE = "prototype_v17_results.md"

# Sample 25 indices spread across the 65 pairs.
# Spread evenly: every ~2.6 indices. This gives early/middle/late coverage.
SAMPLE_INDICES = [
    1, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36,
    39, 42, 45, 48, 51, 54, 57, 60, 62, 63, 64, 65
]

META_PROMPT = """Below are reviews you wrote across many of your past responses. Each one was you looking at something you'd just drafted and deciding whether it sounded like you.

Read across them. What do you notice about how you review yourself? What patterns show up? Are there things you keep flagging? Are there things you don't flag that maybe you should?

Don't summarize each review individually — read the whole set and tell me what you see.

---

{reviews_block}

---

Your observations:"""


def load_soul():
    return SOUL_PATH.read_text() if SOUL_PATH.exists() else ""


def build_system_prompt(soul_text, self_knowledge_text):
    parts = []
    parts.append(
        "\n\nYou are a single-user system. The person you are talking to "
        "right now is the same person from all of your memories."
    )
    if self_knowledge_text:
        parts.append(
            f"\n\nWhat you have learned about yourself through experience:\n\n"
            f"{self_knowledge_text}"
        )
    if soul_text:
        parts.append(f"\n\n{soul_text}")
    return "\n".join(parts)


def parse_v16_results():
    """Pull the 'New review' text from each pair in v16 results."""
    text = Path(V16_RESULTS).read_text()
    pairs = re.split(r'### #(\d+)', text)[1:]
    out = {}
    for j in range(0, len(pairs), 2):
        num = int(pairs[j])
        body = pairs[j+1]
        m = re.search(
            r'\*\*New review[^:]*:\*\*\s*\n+```\n(.*?)\n```',
            body, re.DOTALL
        )
        if m:
            out[num] = m.group(1).strip()
    return out


def get_original_reviews():
    """Pull all 65 original reviews from prod working.db, ordered."""
    conn = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    rows = conn.execute("""
        SELECT sr.review FROM self_reviews sr
        JOIN messages m ON sr.message_id = m.id
        ORDER BY m.timestamp
    """).fetchall()
    conn.close()
    return [r[0] for r in rows]


def format_reviews_block(reviews_by_index):
    """Format a list of (index, text) tuples as a numbered block."""
    parts = []
    for i, (idx, text) in enumerate(reviews_by_index, 1):
        parts.append(f"REVIEW {i}:\n{text}")
    return "\n\n---\n\n".join(parts)


def run_meta_review(client, system_prompt, reviews_block, label):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": META_PROMPT.format(reviews_block=reviews_block)},
    ]
    print(f"Running meta-review: {label}")
    t0 = time.time()
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
    print(f"soul.md: {len(soul_text)} chars")
    print(f"self_knowledge: {len(sk_text)} chars")
    print(f"Sample indices: {SAMPLE_INDICES}")

    originals = get_original_reviews()
    v16 = parse_v16_results()
    print(f"Original reviews loaded: {len(originals)}")
    print(f"v16 reviews loaded: {len(v16)}")

    # Build the two sample sets — same indices, different review text
    orig_sample = [(i, originals[i-1]) for i in SAMPLE_INDICES]
    v16_sample = [(i, v16[i]) for i in SAMPLE_INDICES if i in v16]

    if len(orig_sample) != len(v16_sample):
        print(f"WARNING: orig has {len(orig_sample)}, v16 has {len(v16_sample)}")

    orig_block = format_reviews_block(orig_sample)
    v16_block = format_reviews_block(v16_sample)
    print(f"Original block: {len(orig_block)} chars")
    print(f"v16 block: {len(v16_block)} chars")

    client = ollama.Client(host=OLLAMA_HOST)
    system_prompt = build_system_prompt(soul_text, sk_text)

    orig_meta, orig_t = run_meta_review(
        client, system_prompt, orig_block, "ORIGINAL reviews"
    )
    v16_meta, v16_t = run_meta_review(
        client, system_prompt, v16_block, "v16 reviews"
    )

    out = []
    out.append("# Prototype v17 — Meta-Review Comparison\n")
    out.append(f"- Sample size: {len(SAMPLE_INDICES)} reviews")
    out.append(f"- Sample indices (1-based): {SAMPLE_INDICES}")
    out.append(f"- Model: {MODEL}\n")
    out.append("## What this tests\n")
    out.append("Same 25 review slots, two different review sources.")
    out.append("Nyx reads all 25 in one call and is asked to characterize")
    out.append("the patterns she sees in her own self-critique.\n")
    out.append("Identity grounding (soul.md + self_knowledge + single-user")
    out.append("framing) is identical between both runs. ChromaDB chunks")
    out.append("are NOT included in either run — same shape as v16.\n")
    out.append("---\n")
    out.append("## Meta-review of ORIGINAL reviews (with template propagation)\n")
    out.append(f"_Runtime: {orig_t:.1f}s · Output: {len(orig_meta)} chars_\n")
    out.append("```")
    out.append(orig_meta)
    out.append("```\n")
    out.append("---\n")
    out.append("## Meta-review of v16 reviews (no chunks in review context)\n")
    out.append(f"_Runtime: {v16_t:.1f}s · Output: {len(v16_meta)} chars_\n")
    out.append("```")
    out.append(v16_meta)
    out.append("```\n")
    out.append("---\n")
    out.append("## Sample sets used\n")
    out.append("### Original sample\n")
    out.append("```")
    out.append(orig_block)
    out.append("```\n")
    out.append("### v16 sample\n")
    out.append("```")
    out.append(v16_block)
    out.append("```\n")

    Path(OUTPUT_FILE).write_text("\n".join(out))
    print(f"\nWrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
