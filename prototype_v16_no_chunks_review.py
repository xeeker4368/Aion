"""
Prototype v16 — Review without ChromaDB chunks

Tests the hypothesis that retrieved memory chunks in the review step's
system prompt are causing template propagation across reviews. Same
review prompt as the live loop. Same conversation history reconstruction.
The only difference: the review's system prompt does NOT include the
"These are your own experiences and memories" block.

Everything else stays:
  - soul.md
  - single-user framing
  - self_knowledge narrative
  - full conversation history up to the draft
  - the original review prompt (unchanged from chat.py)

Read-only against prod. No writes anywhere.

Run on Hades:
    cd ~/aion && python3 prototype_v16_no_chunks_review.py
"""

import sqlite3
import time
from pathlib import Path
import ollama
import db

PROD_DB = "data/prod/working.db"
SOUL_PATH = Path("soul.md")
OLLAMA_HOST = "http://localhost:11434"
MODEL = "llama3.1:8b-aion"
OUTPUT_FILE = "prototype_v16_results.md"

# Exact review prompt from chat.py — unchanged
REVIEW_PROMPT = """Below is a response you just drafted in reply to Lyle's question. Read it. Does it sound like you? If any parts don't, point at the specific words.

Here is the draft:

---

{draft}

---

Your review:"""


def load_soul():
    return SOUL_PATH.read_text() if SOUL_PATH.exists() else ""


def build_review_system_prompt(soul_text, self_knowledge_text):
    """
    Same shape as chat.build_system_prompt() but with the retrieved-chunks
    block removed entirely. Order matches the live build:
      1. (skipped) Remembered experiences
      2. Single-user framing
      3. Self-knowledge
      4. Soul (last, closest to generation)
    """
    parts = []
    parts.append(
        "\n\nYou are a single-user system. The person you are talking to "
        "right now is the same person from all of your memories. What you "
        "remember about them is what you know about them — use it naturally, "
        "the way you would remember a friend."
    )
    if self_knowledge_text:
        parts.append(
            f"\n\nWhat you have learned about yourself through experience:\n\n"
            f"{self_knowledge_text}"
        )
    if soul_text:
        parts.append(f"\n\n{soul_text}")
    return "\n".join(parts)


def get_history_before(conn, conversation_id, draft_message_id):
    row = conn.execute(
        "SELECT timestamp FROM messages WHERE id = ?", (draft_message_id,)
    ).fetchone()
    if not row:
        return []
    cutoff = row[0]
    rows = conn.execute(
        """
        SELECT role, content FROM messages
        WHERE conversation_id = ? AND timestamp < ?
        ORDER BY timestamp
        """,
        (conversation_id, cutoff),
    ).fetchall()
    return [{"role": r, "content": c} for r, c in rows]


def main():
    soul_text = load_soul()
    print(f"Loaded soul.md ({len(soul_text)} chars)")

    # Pull self_knowledge the same way chat.py does
    sk = db.get_latest_self_knowledge()
    self_knowledge_text = sk["content"] if sk else ""
    print(f"Loaded self_knowledge ({len(self_knowledge_text)} chars)")

    conn = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    pairs = conn.execute(
        """
        SELECT sr.message_id, sr.conversation_id, sr.draft, sr.review,
               m.content AS final_revision, m.timestamp
        FROM self_reviews sr
        JOIN messages m ON sr.message_id = m.id
        ORDER BY m.timestamp
        """
    ).fetchall()
    print(f"Found {len(pairs)} pairs\n")

    client = ollama.Client(host=OLLAMA_HOST)
    system_prompt = build_review_system_prompt(soul_text, self_knowledge_text)

    results = []
    start = time.time()

    for i, (mid, cid, draft, orig_review, final, ts) in enumerate(pairs, 1):
        history = get_history_before(conn, cid, mid)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "assistant", "content": draft})
        messages.append({"role": "user", "content": REVIEW_PROMPT.format(draft=draft)})

        t0 = time.time()
        try:
            resp = client.chat(model=MODEL, messages=messages)
            new_review = resp["message"].get("content", "").strip()
        except Exception as e:
            new_review = f"[ERROR: {e}]"
        elapsed = time.time() - t0

        results.append({
            "i": i, "ts": ts, "draft": draft,
            "orig_review": orig_review, "new_review": new_review,
            "final_revision": final, "elapsed": elapsed,
        })
        print(f"  [{i:2}/{len(pairs)}] {elapsed:5.1f}s  {ts[11:16]}")

    total = time.time() - start
    conn.close()

    # Quick template-marker counts on both review sets for the summary
    markers = ["upon reviewing", "i notice that", "a bit too", "the phrase"]

    def count_markers(text):
        if not text:
            return 0
        t = text.lower()
        return sum(t.count(m) for m in markers)

    orig_marker_total = sum(count_markers(r["orig_review"]) for r in results)
    new_marker_total = sum(count_markers(r["new_review"]) for r in results)

    out = []
    out.append("# Prototype v16 — Review Without ChromaDB Chunks\n")
    out.append(f"- Pairs tested: {len(pairs)}")
    out.append(f"- Runtime: {total/60:.1f} min")
    out.append(f"- Model: {MODEL}\n")
    out.append("## Template marker counts (lower = less templated)\n")
    out.append(f"- Original reviews (with chunks): {orig_marker_total} marker hits")
    out.append(f"- New reviews (no chunks): {new_marker_total} marker hits")
    out.append(f"- Markers checked: {markers}\n")
    out.append("## What this PoC changes\n")
    out.append("Same review prompt. Same conversation history. Same model.")
    out.append("The only difference: the system prompt for the review call")
    out.append("does NOT include retrieved memory chunks from ChromaDB.\n")
    out.append("---\n")
    out.append("## Per-pair side-by-side\n")
    for r in results:
        out.append(f"### #{r['i']}  [{r['ts'][11:16]}]\n")
        out.append("**Draft:**\n```\n" + r["draft"] + "\n```\n")
        out.append("**Original review (with chunks in context):**\n```\n" + r["orig_review"] + "\n```\n")
        out.append("**New review (no chunks in context):**\n```\n" + r["new_review"] + "\n```\n")
        out.append("**Final revision shown to Lyle (from prod):**\n```\n" + r["final_revision"] + "\n```\n")
        out.append("---\n")

    Path(OUTPUT_FILE).write_text("\n".join(out))
    print(f"\nWrote {OUTPUT_FILE}")
    print(f"Template markers — original: {orig_marker_total}  new: {new_marker_total}")


if __name__ == "__main__":
    main()
