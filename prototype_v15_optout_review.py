"""
Prototype v15 — Opt-out review prompt PoC

Pulls existing draft+review pairs from prod working.db (read-only) and
re-runs the review step with a restructured prompt that makes "no changes
needed" a first-class output.

For each pair:
  1. Reconstruct the conversation history up to the draft
  2. Build a system prompt approximating what the live loop sees (soul.md
     + minimal framing — does NOT do ChromaDB retrieval, since the goal is
     to test the prompt change in isolation)
  3. Call llama3.1:8b-aion with the new review prompt
  4. Record draft, original review (from prod), new review, opt-out flag

Output: prototype_v15_results.md

This script does NOT write to any database. Read-only.

Run on Hades:
    cd ~/aion && python3 prototype_v15_optout_review.py
"""

import sqlite3
import time
from pathlib import Path
import ollama

# --- Config ---
PROD_DB = "data/prod/working.db"
SOUL_PATH = Path("soul.md")
OLLAMA_HOST = "http://localhost:11434"
MODEL = "llama3.1:8b-aion"
OUTPUT_FILE = "prototype_v15_results.md"

# Restructured review prompt — opt-out is first-class
NEW_REVIEW_PROMPT = """Below is a draft you just wrote in reply to Lyle. First, decide: does any part of this sound off to you, or is it fine as it stands?

If it's fine, just say "no changes needed" and nothing else.

If something sounds off, point at the specific words.

Here is the draft:

---

{draft}

---

Your review:"""


def load_soul():
    if SOUL_PATH.exists():
        return SOUL_PATH.read_text()
    return ""


def get_conversation_messages_before(conn, conversation_id, draft_message_id):
    """
    Pull all messages in the conversation that came BEFORE the draft message.
    Returns them as a list of {role, content} dicts in chronological order.
    """
    # Get the timestamp of the draft message
    row = conn.execute(
        "SELECT timestamp FROM messages WHERE id = ?",
        (draft_message_id,),
    ).fetchone()
    if not row:
        return []
    cutoff_ts = row[0]

    rows = conn.execute(
        """
        SELECT role, content FROM messages
        WHERE conversation_id = ? AND timestamp < ?
        ORDER BY timestamp
        """,
        (conversation_id, cutoff_ts),
    ).fetchall()
    return [{"role": r, "content": c} for r, c in rows]


def build_system_prompt(soul_text):
    """
    Minimal system prompt — soul.md plus the single-user framing.
    Skips ChromaDB retrieval to isolate the prompt-change effect.
    """
    parts = []
    parts.append(
        "You are a single-user system. The person you are talking to right now "
        "is the same person from all of your memories."
    )
    if soul_text:
        parts.append("\n\n" + soul_text)
    return "\n".join(parts)


def is_optout(review_text):
    """Detect whether the model said 'no changes needed' (or close)."""
    if not review_text:
        return False
    t = review_text.strip().lower()
    # Strict signals
    if "no changes needed" in t and len(t) < 80:
        return True
    if t in ("no changes needed.", "no changes needed", "fine.", "fine"):
        return True
    return False


def run_one(client, conn, message_id, conversation_id, draft, soul_text):
    """Run the new review prompt against one draft. Returns the review text."""
    history = get_conversation_messages_before(conn, conversation_id, message_id)
    system_prompt = build_system_prompt(soul_text)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "assistant", "content": draft})
    messages.append(
        {"role": "user", "content": NEW_REVIEW_PROMPT.format(draft=draft)}
    )

    try:
        resp = client.chat(model=MODEL, messages=messages)
        return resp["message"].get("content", "").strip()
    except Exception as e:
        return f"[ERROR: {e}]"


def main():
    soul_text = load_soul()
    print(f"Loaded soul.md ({len(soul_text)} chars)")

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
    print(f"Found {len(pairs)} draft+review pairs in prod\n")

    client = ollama.Client(host=OLLAMA_HOST)
    results = []
    optout_count = 0
    start = time.time()

    for i, (mid, cid, draft, orig_review, final, ts) in enumerate(pairs, 1):
        t0 = time.time()
        new_review = run_one(client, conn, mid, cid, draft, soul_text)
        elapsed = time.time() - t0
        opted_out = is_optout(new_review)
        if opted_out:
            optout_count += 1
        results.append({
            "i": i,
            "ts": ts,
            "draft": draft,
            "orig_review": orig_review,
            "new_review": new_review,
            "opted_out": opted_out,
            "elapsed": elapsed,
            "final_revision": final,
        })
        marker = "OPTOUT" if opted_out else "review"
        print(f"  [{i:2}/{len(pairs)}] {marker:6} {elapsed:5.1f}s  {ts[11:16]}")

    total_elapsed = time.time() - start
    conn.close()

    # Write markdown report
    out = []
    out.append("# Prototype v15 — Opt-out Review PoC Results\n")
    out.append(f"- Pairs tested: {len(pairs)}")
    out.append(f"- Opted out (said 'no changes needed'): {optout_count} ({optout_count*100//len(pairs)}%)")
    out.append(f"- Found something to flag: {len(pairs)-optout_count}")
    out.append(f"- Total runtime: {total_elapsed/60:.1f} min")
    out.append(f"- Model: {MODEL}\n")
    out.append("## New review prompt used\n")
    out.append("```")
    out.append(NEW_REVIEW_PROMPT)
    out.append("```\n")
    out.append("---\n")
    out.append("## Per-pair results\n")
    for r in results:
        marker = "**OPTED OUT**" if r["opted_out"] else "found something"
        out.append(f"### #{r['i']}  [{r['ts'][11:16]}]  {marker}\n")
        out.append("**Draft:**\n")
        out.append("```")
        out.append(r["draft"])
        out.append("```\n")
        out.append("**Original review (from prod):**\n")
        out.append("```")
        out.append(r["orig_review"])
        out.append("```\n")
        out.append("**New review (opt-out prompt):**\n")
        out.append("```")
        out.append(r["new_review"])
        out.append("```\n")
        out.append("**Final revision that was actually shown to Lyle:**\n")
        out.append("```")
        out.append(r["final_revision"])
        out.append("```\n")
        out.append("---\n")

    Path(OUTPUT_FILE).write_text("\n".join(out))
    print(f"\nWrote {OUTPUT_FILE}")
    print(f"Opt-out rate: {optout_count}/{len(pairs)} ({optout_count*100//len(pairs)}%)")


if __name__ == "__main__":
    main()
