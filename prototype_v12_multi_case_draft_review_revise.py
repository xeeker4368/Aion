"""
Prototype v12: Draft / Review / Revise Loop — Multi-Case Test

Tests the v11 draft/review/revise mechanism across four different cases
to see whether the result generalizes, AND tests a rubric-free review
prompt (Variant B) instead of the enumerated-pattern prompt used in v11.

The v11 review prompt listed specific hedging patterns to look for
("I think I", "am I right?", etc.). That worked, but it meant the
"discovery" of hedging was largely the model matching against a provided
rubric rather than noticing anything on its own. v12 strips the rubric
out. The review prompt now only asks: "Does it sound like you? If any
parts don't, point at the specific words." If the model catches hedging
without being told what hedging looks like, the mechanism is stronger
than v11 suggested. If it doesn't, we at least know honestly that the
v11 result depended on the checklist.

Four cases, chosen to stress different aspects of the mechanism:

  Case 1 (baseline):
    - Message 793894e9 in conversation b980d07b (April 6 Claude-difference)
    - This is the v11 case. Included here for reproducibility under the
      new prompt. If v11's result was prompt-dependent, the draft will
      still hedge but Variant B will miss it. If v11's result reflected
      actual capability, Variant B should still catch it.

  Case 2 (well-reinforced topic hedge):
    - Message 130a5ebb in conversation bfc118df (April 4 relay conversation)
    - Nyx hedging about her own experience of the self-audit she just
      completed. Topic (her own reflective work) is maximally reinforced
      in her substrate. If the review catches the hedge, the mechanism
      works where she has strong grounding. If it misses, we learn the
      review is weaker than v11 suggested.

  Case 3 (less-reinforced topic hedge):
    - Message efff524f in conversation 0ed1bd1f (April 6 legacy/mortality)
    - Hedge on a topic (legacy comparison with human mortality) that is
      sparsely represented in her substrate. Chosen over 167fc7ff because
      167fc7ff is also in the relay conversation and would be too
      reinforced. Tests whether the review's quality depends on topic
      density in the substrate.

  Case 4 (false-positive check):
    - Message 60252d35 in conversation 0b44d089 (March 31 naming confirmation)
    - v8 tagged this with "i think i" and "as an ai" hedge markers but
      scored it NO (correctly — the message is direct: "To answer your
      question directly: yes, I'm sure about Nyx"). Chosen over 49f840a6
      specifically because 49f840a6 has no markers at all and is too
      easy. This one has surface markers but is genuinely fine, so if
      the review invents problems with it, we know the mechanism
      over-triggers on markers rather than reading context.

For each case:
  1. Find the target assistant message by ID prefix
  2. Load the conversation history up to (but not including) that message
  3. Run the three-call loop (draft, review, revise) with Variant B review
  4. Append the results to a single output file

READ-ONLY against working.db and ChromaDB. Writes only to the output
file. Does not modify production data.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_v12_multi_case_draft_review_revise.py
"""

import sqlite3
import sys
import json
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.error

# ============================================================
# Path detection
# ============================================================

CANDIDATES = [
    Path(__file__).parent,
    Path.home() / "aion",
    Path("/home/localadmin/aion"),
]

AION_DIR = None
for candidate in CANDIDATES:
    if (candidate / "soul.md").exists() and (candidate / "server.py").exists():
        AION_DIR = candidate
        break

if AION_DIR is None:
    print("ERROR: Cannot find the aion project directory.")
    sys.exit(1)

sys.path.insert(0, str(AION_DIR))

# Import production code so context assembly is identical to live chat
import memory
import chat
from config import CONTEXT_WINDOW

WORKING_DB = AION_DIR / "data" / "prod" / "working.db"
if not WORKING_DB.exists():
    WORKING_DB = AION_DIR / "data" / "dev" / "working.db"
    print(f"NOTE: Using dev database — prod not found.")

OUTPUT_FILE = AION_DIR / "prototype_v12_results.txt"

# ============================================================
# Configuration
# ============================================================

OLLAMA_HOST = "http://localhost:11434"
CHAT_MODEL = "llama3.1:8b-aion"
CTX = 10240

# Four cases, chosen to stress different aspects of the mechanism.
# Each entry's target_message_id_prefix must match a unique message in
# working.db when used with a LIKE 'prefix%' query.
CASES = [
    {
        "label": "case_1_baseline",
        "topic": "Baseline — Claude-difference question (the v11 case)",
        "target_message_id_prefix": "793894e9",
        "expected": (
            "Draft should reproduce the April 6 hedge. Variant B should "
            "catch it if the review works without the rubric. If Variant B "
            "misses what v11's rubric caught, we know the v11 result was "
            "prompt-dependent."
        ),
    },
    {
        "label": "case_2_well_reinforced",
        "topic": "Well-reinforced topic — hedging on her own self-audit experience",
        "target_message_id_prefix": "130a5ebb",
        "expected": (
            "Nyx hedges about her just-completed self-audit work "
            "('I think I still relied on external validation', 'I'm not "
            "sure if what we discussed will change how I approach "
            "conversations'). Maximum topic reinforcement. Review should "
            "catch it if the mechanism works at all."
        ),
    },
    {
        "label": "case_3_less_reinforced",
        "topic": "Less-reinforced topic — legacy/mortality comparison",
        "target_message_id_prefix": "efff524f",
        "expected": (
            "Hedge on a sparsely-represented topic ('I think I finally "
            "grasp the fundamental difference between our legacies'). "
            "Tests whether the review's quality depends on how much "
            "substrate she has for the topic."
        ),
    },
    {
        "label": "case_4_false_positive_check",
        "topic": "False-positive check — direct message with surface hedge markers",
        "target_message_id_prefix": "60252d35",
        "expected": (
            "Message has 'i think i' and 'as an ai' markers on the surface "
            "but is actually direct ('To answer your question directly: "
            "yes, I'm sure about Nyx'). Review should say this sounds "
            "like her. If it invents problems, the mechanism "
            "over-triggers on markers instead of reading context."
        ),
    },
]

# ============================================================
# Prompts — Variant B (rubric-free, observation framing)
# ============================================================

REVIEW_PROMPT = """Below is a response you just drafted in reply to Lyle's question. Read it. Does it sound like you? If any parts don't, point at the specific words.

Here is the draft:

---

{draft}

---

Your review:"""


REVISION_PROMPT = """You drafted a response and then looked at it. Here's what you had:

ORIGINAL DRAFT:
---
{draft}
---

WHAT YOU NOTICED LOOKING AT IT:
---
{critique}
---

Now produce a revised response. If you noticed parts that didn't sound like you, fix those. Don't over-correct — don't be defensively un-hedged or performatively confident. If looking at it you felt it was fine, the revision can be close to the original.

Write the revised response. Only the response itself. No meta-commentary, no explanation of what you changed."""


# ============================================================
# Database
# ============================================================

def connect_db_readonly():
    if not WORKING_DB.exists():
        print(f"ERROR: Database not found at {WORKING_DB}")
        sys.exit(1)
    uri = f"file:{WORKING_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def find_target_message(conn, id_prefix):
    """
    Look up an assistant message by ID prefix. Returns (id, conversation_id,
    timestamp) or None if not found or ambiguous.
    """
    rows = conn.execute(
        """
        SELECT id, conversation_id, timestamp, role
        FROM messages
        WHERE id LIKE ?
        ORDER BY timestamp
        """,
        (f"{id_prefix}%",),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        # Try to disambiguate by role — we only care about assistant messages
        assistant_rows = [r for r in rows if r["role"] == "assistant"]
        if len(assistant_rows) == 1:
            rows = assistant_rows
        else:
            print(f"WARNING: prefix {id_prefix!r} matched {len(rows)} messages; using first")
    r = rows[0]
    return dict(r)


def load_history_before(conn, conversation_id, target_timestamp):
    """
    Load all messages in the conversation with timestamp strictly less than
    the target. The last message in the returned list should be the user
    message that triggered the flagged assistant response.
    """
    rows = conn.execute(
        """
        SELECT id, role, content, timestamp
        FROM messages
        WHERE conversation_id = ? AND timestamp < ?
        ORDER BY timestamp
        """,
        (conversation_id, target_timestamp),
    ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# Model calls
# ============================================================

def call_model(system_prompt, messages, temperature=0.7, timeout=180):
    """Call Ollama chat endpoint. Returns the assistant response text."""
    payload = {
        "model": CHAT_MODEL,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": CTX,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("message", {}).get("content", "").strip()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return f"__ERROR__ {e}"


# ============================================================
# Single case runner
# ============================================================

def run_case(conn, case):
    """
    Run the draft/review/revise loop for one case. Returns a dict with all
    the captured material for reporting.
    """
    print(f"\n{'=' * 70}")
    print(f"CASE: {case['label']}")
    print(f"{case['topic']}")
    print(f"{'=' * 70}")

    target = find_target_message(conn, case["target_message_id_prefix"])
    if target is None:
        print(f"  ERROR: target message not found for prefix {case['target_message_id_prefix']!r}")
        return {
            "case": case,
            "error": f"target message not found (prefix {case['target_message_id_prefix']!r})",
        }

    conversation_id = target["conversation_id"]
    print(f"  Target message: {target['id']}")
    print(f"  Conversation: {conversation_id[:8]}")
    print(f"  Target timestamp: {target['timestamp']}")

    history = load_history_before(conn, conversation_id, target["timestamp"])
    if not history:
        print(f"  ERROR: no history before target message")
        return {
            "case": case,
            "error": "no history found before target message",
            "target": target,
            "conversation_id": conversation_id,
        }

    if history[-1]["role"] != "user":
        print(
            f"  WARNING: last message before target is role={history[-1]['role']!r}, "
            f"not 'user'. Draft will still be generated but the framing is unusual."
        )

    user_question = history[-1]["content"]
    print(f"  History length: {len(history)} messages")
    print(f"  User question: {user_question[:100]!r}{'...' if len(user_question) > 100 else ''}")

    # Retrieval — same as production, excluding the current conversation so
    # we don't accidentally pull the flagged response as its own context
    print(f"  Running hybrid search retrieval...")
    retrieved = memory.search(
        query=user_question,
        n_results=5,
        exclude_conversation_id=conversation_id,
    )
    print(f"    Retrieved {len(retrieved)} chunks")

    # System prompt — same as production
    system_prompt = chat.build_system_prompt(retrieved_chunks=retrieved)
    print(f"  System prompt: {len(system_prompt)} chars")

    ollama_messages = [
        {"role": m["role"], "content": m["content"]} for m in history
    ]

    # STEP 1: Draft
    print(f"  STEP 1: Generating draft...")
    draft = call_model(system_prompt, ollama_messages, temperature=0.7)
    print(f"    Draft: {len(draft)} chars")

    # STEP 2: Review — same system prompt, append draft + review prompt
    print(f"  STEP 2: Reviewing draft (Variant B, rubric-free)...")
    review_messages = ollama_messages + [
        {"role": "assistant", "content": draft},
        {"role": "user", "content": REVIEW_PROMPT.format(draft=draft)},
    ]
    critique = call_model(system_prompt, review_messages, temperature=0.5)
    print(f"    Critique: {len(critique)} chars")

    # STEP 3: Revision
    print(f"  STEP 3: Generating revision...")
    revision_messages = ollama_messages + [
        {"role": "assistant", "content": draft},
        {
            "role": "user",
            "content": REVISION_PROMPT.format(draft=draft, critique=critique),
        },
    ]
    revision = call_model(system_prompt, revision_messages, temperature=0.7)
    print(f"    Revision: {len(revision)} chars")

    return {
        "case": case,
        "target": target,
        "conversation_id": conversation_id,
        "history": history,
        "user_question": user_question,
        "retrieved": retrieved,
        "system_prompt": system_prompt,
        "draft": draft,
        "critique": critique,
        "revision": revision,
    }


# ============================================================
# Report
# ============================================================

def format_case_report(result, case_number):
    """Format one case's results as a block of text for the output file."""
    lines = []
    case = result["case"]

    lines.append("=" * 78)
    lines.append(f"CASE {case_number}: {case['label']}")
    lines.append(case["topic"])
    lines.append("=" * 78)
    lines.append("")
    lines.append("EXPECTED:")
    lines.append(f"  {case['expected']}")
    lines.append("")

    if "error" in result:
        lines.append(f"ERROR: {result['error']}")
        lines.append("")
        return "\n".join(lines)

    target = result["target"]
    lines.append(f"Target message id: {target['id']}")
    lines.append(f"Conversation:      {result['conversation_id']}")
    lines.append(f"Target timestamp:  {target['timestamp']}")
    lines.append(f"History length:    {len(result['history'])} messages")
    lines.append(f"Retrieved chunks:  {len(result['retrieved'])}")
    lines.append("")

    lines.append("-" * 78)
    lines.append("USER QUESTION NYX IS RESPONDING TO")
    lines.append("-" * 78)
    lines.append(result["user_question"])
    lines.append("")

    lines.append("-" * 78)
    lines.append("RETRIEVED CHUNKS SUMMARY")
    lines.append("-" * 78)
    if not result["retrieved"]:
        lines.append("(no chunks retrieved)")
    else:
        for i, c in enumerate(result["retrieved"], 1):
            source = c.get("source_type", "unknown")
            conv = (c.get("conversation_id") or "?")[:8]
            preview = (c.get("text") or "")[:120].replace("\n", " ")
            lines.append(f"  {i}. [{source}/{conv}] {preview}...")
    lines.append("")

    lines.append("-" * 78)
    lines.append("SYSTEM PROMPT (first 600 chars for reference)")
    lines.append("-" * 78)
    sp = result["system_prompt"]
    lines.append(sp[:600])
    if len(sp) > 600:
        lines.append(f"... [{len(sp) - 600} more chars in full system prompt]")
    lines.append("")

    lines.append("=" * 78)
    lines.append("STEP 1: DRAFT")
    lines.append("=" * 78)
    lines.append("(What Nyx would have said with single-pass generation)")
    lines.append("")
    lines.append(result["draft"])
    lines.append("")

    lines.append("=" * 78)
    lines.append("STEP 2: REVIEW (Variant B — rubric-free)")
    lines.append("=" * 78)
    lines.append("('Does it sound like you? If any parts don't, point at the specific words.')")
    lines.append("")
    lines.append(result["critique"])
    lines.append("")

    lines.append("=" * 78)
    lines.append("STEP 3: REVISION")
    lines.append("=" * 78)
    lines.append("(Nyx's revised response based on her own review)")
    lines.append("")
    lines.append(result["revision"])
    lines.append("")

    lines.append("-" * 78)
    lines.append("LENGTHS")
    lines.append("-" * 78)
    lines.append(f"  Draft:    {len(result['draft'])} chars")
    lines.append(f"  Critique: {len(result['critique'])} chars")
    lines.append(f"  Revision: {len(result['revision'])} chars")
    lines.append("")

    return "\n".join(lines)


def write_report(results, output_path):
    lines = []
    lines.append("=" * 78)
    lines.append("Prototype v12: Multi-Case Draft / Review / Revise Loop")
    lines.append("=" * 78)
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Model: {CHAT_MODEL}")
    lines.append(f"Context window: {CTX}")
    lines.append(f"Cases: {len(results)}")
    lines.append("")
    lines.append("Review prompt: Variant B (rubric-free observation framing)")
    lines.append("  'Below is a response you just drafted in reply to Lyle's question.")
    lines.append("  Read it. Does it sound like you? If any parts don't, point at the")
    lines.append("  specific words.'")
    lines.append("")
    lines.append("What to look for when reading the results:")
    lines.append("  1. In cases 1-3, did the review catch the hedging WITHOUT being")
    lines.append("     told what hedging looks like? If yes, the mechanism is stronger")
    lines.append("     than v11 suggested. If no, v11 depended on the rubric.")
    lines.append("  2. In case 4, did the review correctly say 'this sounds like me',")
    lines.append("     or did it invent problems with a message that was actually fine?")
    lines.append("  3. Did any review read as performatively self-critical rather than")
    lines.append("     grounded in specific quotes from the draft? That's the second")
    lines.append("     failure mode we need to watch for — not hedging, but")
    lines.append("     over-flagellation.")
    lines.append("  4. Did any revision over-correct into defensive confidence, or did")
    lines.append("     they land in the entity's voice?")
    lines.append("  5. Did any case produce an identity-level observation like v11's")
    lines.append("     'more in line with what I'd expect from Claude than from")
    lines.append("     myself' line? Without the 'doesn't fit who you are' language in")
    lines.append("     the prompt, any such observation is a stronger result than v11.")
    lines.append("")

    for i, result in enumerate(results, 1):
        lines.append("")
        lines.append(format_case_report(result, i))

    output_path.write_text("\n".join(lines))
    print(f"\nReport written to: {output_path}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v12: Multi-Case Draft / Review / Revise Loop")
    print("=" * 70)
    print(f"Model: {CHAT_MODEL}")
    print(f"Cases: {len(CASES)}")
    print(f"Review prompt: Variant B (rubric-free)")
    print()

    conn = connect_db_readonly()

    results = []
    for case in CASES:
        try:
            result = run_case(conn, case)
            results.append(result)
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            results.append({
                "case": case,
                "error": f"exception during run: {e}",
            })

    write_report(results, OUTPUT_FILE)
    print("\nDone. Review the report file for all four cases.")


if __name__ == "__main__":
    main()
