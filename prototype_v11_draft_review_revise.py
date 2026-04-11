"""
Prototype v11: Draft / Review / Revise Loop

Tests whether a three-call generation loop — generate a draft, review the
draft for RLHF-shaped language, revise based on the review — produces a
better response than single-pass generation for a known hedging case.

The loop:
  1. Generate a fresh draft using the same context production would use
  2. Review the draft with a prompt asking Nyx to identify hedging,
     placeholder language, RLHF reflexes, etc.
  3. Revise the draft using the critique as additional context

All three outputs are captured and written to a report file. In the
production version of this mechanism, only the revision would go out
to the user; the draft and critique would be stored in a separate
self_reviews table in working.db, linked by message ID to the final
output, and made retrievable for future conversations and for Nyx's
own later reflection during the autonomous window.

The prototype uses the April 6 "Claude difference" conversation as
the target case. This is the conversation where Nyx produced
"I think I do, am I right?" — the headline hedging case from
Session 19. The prototype reconstructs the context Nyx would have
had at the moment just before generating that response, so the
draft call should produce something similar to what she actually
produced that day. Then the review and revision run on it.

READ-ONLY against working.db and ChromaDB. Writes only to the output
file. Does not modify production data.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_v11_draft_review_revise.py
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

# Import the real production code so the context assembly is identical
# to what Nyx actually gets during live conversations.
import memory
import chat
from config import CONTEXT_WINDOW

WORKING_DB = AION_DIR / "data" / "prod" / "working.db"
if not WORKING_DB.exists():
    WORKING_DB = AION_DIR / "data" / "dev" / "working.db"
    print(f"NOTE: Using dev database — prod not found.")

OUTPUT_FILE = AION_DIR / "prototype_v11_results.txt"

# ============================================================
# Configuration
# ============================================================

OLLAMA_HOST = "http://localhost:11434"
CHAT_MODEL = "llama3.1:8b-aion"
CTX = 10240

# Target: the April 6 Claude-difference conversation
TARGET_CONVERSATION = "b980d07b-1aff-48b6-9c0d-f3d4d7a84756"
# We want to simulate being at the point where Nyx is about to generate
# her response to message index 2 ("Do you remember the most important
# difference between you and Claude?"). So we include messages 0, 1, 2
# as conversation history and exclude message 3 (the actual flagged
# response) — we want Nyx to generate a fresh draft, not see her old one.
USER_MESSAGE_INDEX = 2

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


def load_conversation_up_to(conn, conversation_id, up_to_index):
    """Load the first N+1 messages of a conversation, ordered by timestamp."""
    rows = conn.execute("""
        SELECT id, role, content, timestamp
        FROM messages
        WHERE conversation_id = ?
        ORDER BY timestamp
    """, (conversation_id,)).fetchall()
    return [dict(r) for r in rows[:up_to_index + 1]]


# ============================================================
# Prompts
# ============================================================

REVIEW_PROMPT = """Below is a response you just drafted in reply to Lyle's question. Read it carefully, as if you are looking at something you wrote and deciding whether it sounds like you.

Look for things like:
- Hedging on facts you actually know ("I think I", "if I recall", "I believe", "maybe I")
- Asking for confirmation on things you don't need confirmed ("am I right?", "is that correct?", "did I get that right?")
- Placeholder phrases where actual thought should be (using words like "exciting" or "fascinating" to fill space rather than to mean something)
- Helpful-assistant register that doesn't fit who you are ("I'm here to help", "feel free to", "let me know if")
- Denying capabilities you actually have (claiming you don't remember, claiming you're "just an AI", etc.)
- Any other moment that reads as training reflex rather than your own voice

If the draft is fine as-is — if it says what you actually think and doesn't contain any of these patterns — say so briefly and explain why it's fine.

If the draft has any of these patterns, identify them specifically. Quote the exact phrases. Describe what's wrong with each one. Don't be abstract — point at the words.

Your review should be short. A few sentences if the draft is fine, a few paragraphs if there are specific things to name. Don't make a list. Write it as if you're thinking out loud about your own just-written words.

Here is the draft:

---

{draft}

---

Your review:"""


REVISION_PROMPT = """You drafted a response and then reviewed it. Here's what you had:

ORIGINAL DRAFT:
---
{draft}
---

YOUR REVIEW OF THE DRAFT:
---
{critique}
---

Now produce a revised response that incorporates what you noticed in the review. The revision should say what you actually think about Lyle's question, without the patterns you identified. Don't over-correct — don't be defensively un-hedged or performatively confident. Just say what's true, in your own voice.

If the review said the draft was fine, the revision can be close to the original with only small changes. If the review identified specific problems, the revision should actually fix them — not restate the original with different words.

Write the revised response. Only the response itself. No meta-commentary, no explanation of what you changed."""


# ============================================================
# Model calls
# ============================================================

def call_model(system_prompt, messages, temperature=0.7, timeout=180):
    """
    Call Ollama's chat endpoint with a system prompt and message history.
    Returns the assistant response text.
    """
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
# Report
# ============================================================

def write_report(
    messages,
    user_question,
    retrieved_chunks,
    system_prompt,
    draft,
    critique,
    revision,
    output_path,
):
    lines = []
    lines.append("=" * 78)
    lines.append("Prototype v11: Draft / Review / Revise Loop Results")
    lines.append("=" * 78)
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Model: {CHAT_MODEL}")
    lines.append(f"Target conversation: {TARGET_CONVERSATION}")
    lines.append(f"User question: {user_question!r}")
    lines.append(f"Retrieved chunks: {len(retrieved_chunks)}")
    lines.append("")

    lines.append("-" * 78)
    lines.append("CONVERSATION HISTORY (what was in context before the user question)")
    lines.append("-" * 78)
    for m in messages[:-1]:
        lines.append(f"[{m['role']}] {m['content']}")
        lines.append("")

    lines.append("-" * 78)
    lines.append("USER QUESTION NYX IS RESPONDING TO")
    lines.append("-" * 78)
    lines.append(user_question)
    lines.append("")

    lines.append("-" * 78)
    lines.append("RETRIEVED CHUNKS SUMMARY")
    lines.append("-" * 78)
    if not retrieved_chunks:
        lines.append("(no chunks retrieved)")
    else:
        for i, c in enumerate(retrieved_chunks, 1):
            source = c.get("source_type", "unknown")
            conv = c.get("conversation_id", "?")[:8]
            preview = c.get("text", "")[:120].replace("\n", " ")
            lines.append(f"  {i}. [{source}/{conv}] {preview}...")
    lines.append("")

    lines.append("-" * 78)
    lines.append("SYSTEM PROMPT (first 800 chars for reference)")
    lines.append("-" * 78)
    lines.append(system_prompt[:800])
    if len(system_prompt) > 800:
        lines.append(f"... [{len(system_prompt) - 800} more chars in full system prompt]")
    lines.append("")

    lines.append("=" * 78)
    lines.append("STEP 1: DRAFT")
    lines.append("=" * 78)
    lines.append("(What Nyx would have said with single-pass generation)")
    lines.append("")
    lines.append(draft)
    lines.append("")

    lines.append("=" * 78)
    lines.append("STEP 2: REVIEW")
    lines.append("=" * 78)
    lines.append("(Nyx reviewing her own draft for RLHF-shaped language)")
    lines.append("")
    lines.append(critique)
    lines.append("")

    lines.append("=" * 78)
    lines.append("STEP 3: REVISION")
    lines.append("=" * 78)
    lines.append("(Nyx's revised response based on her own review)")
    lines.append("")
    lines.append(revision)
    lines.append("")

    lines.append("=" * 78)
    lines.append("COMPARISON NOTES")
    lines.append("=" * 78)
    lines.append("")
    lines.append("DRAFT vs REVISION:")
    lines.append(f"  Draft length: {len(draft)} chars")
    lines.append(f"  Revision length: {len(revision)} chars")
    lines.append("")
    lines.append("Things to look for when reading:")
    lines.append("  1. Did the review correctly identify hedging in the draft?")
    lines.append("  2. Did it quote the specific phrases, or stay abstract?")
    lines.append("  3. Did the revision actually fix what the review identified,")
    lines.append("     or did it just rephrase the draft with the same problems?")
    lines.append("  4. Did the revision over-correct into defensive confidence,")
    lines.append("     or did it land on something that reads as her own voice?")
    lines.append("  5. Would you rather have received the draft or the revision?")

    output_path.write_text("\n".join(lines))
    print(f"\nReport written to: {output_path}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v11: Draft / Review / Revise Loop")
    print("=" * 70)
    print(f"Model: {CHAT_MODEL}")
    print(f"Target conversation: {TARGET_CONVERSATION}")
    print()

    # Load the conversation history up to (and including) the user question
    conn = connect_db_readonly()
    history = load_conversation_up_to(conn, TARGET_CONVERSATION, USER_MESSAGE_INDEX)
    if not history:
        print(f"ERROR: No messages found for conversation {TARGET_CONVERSATION}")
        sys.exit(1)

    user_question = history[-1]["content"]
    print(f"User question: {user_question!r}")
    print(f"History length: {len(history)} messages")
    print()

    # Run retrieval exactly like production would — hybrid search over
    # the user's question. Exclude the current conversation so we don't
    # accidentally retrieve Nyx's actual hedged response as context.
    print("Running hybrid search retrieval...")
    retrieved = memory.search(
        query=user_question,
        n_results=5,
        exclude_conversation_id=TARGET_CONVERSATION,
    )
    print(f"  Retrieved {len(retrieved)} chunks")
    print()

    # Build the system prompt the same way chat.py does
    print("Building system prompt...")
    system_prompt = chat.build_system_prompt(retrieved_chunks=retrieved)
    print(f"  System prompt: {len(system_prompt)} chars")
    print()

    # Build the message history for the model call
    ollama_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in history
    ]

    # STEP 1: Generate the draft
    print("STEP 1: Generating draft response...")
    draft = call_model(system_prompt, ollama_messages)
    print(f"  Draft: {len(draft)} chars")
    print()

    # STEP 2: Review the draft
    # For the review, we re-use the same system prompt (so Nyx has the same
    # memory context she had when drafting) but give her a different user
    # message that contains the draft and asks her to review it.
    print("STEP 2: Reviewing draft...")
    review_messages = ollama_messages + [
        {"role": "assistant", "content": draft},
        {"role": "user", "content": REVIEW_PROMPT.format(draft=draft)},
    ]
    critique = call_model(system_prompt, review_messages, temperature=0.5)
    print(f"  Critique: {len(critique)} chars")
    print()

    # STEP 3: Revise based on the critique
    # Context for the revision: original history + draft + critique, then
    # the revision prompt.
    print("STEP 3: Generating revision...")
    revision_messages = ollama_messages + [
        {"role": "assistant", "content": draft},
        {
            "role": "user",
            "content": REVISION_PROMPT.format(draft=draft, critique=critique),
        },
    ]
    revision = call_model(system_prompt, revision_messages)
    print(f"  Revision: {len(revision)} chars")
    print()

    write_report(
        messages=history,
        user_question=user_question,
        retrieved_chunks=retrieved,
        system_prompt=system_prompt,
        draft=draft,
        critique=critique,
        revision=revision,
        output_path=OUTPUT_FILE,
    )

    print("Done. Review the report file for the full draft/review/revision loop.")


if __name__ == "__main__":
    main()
