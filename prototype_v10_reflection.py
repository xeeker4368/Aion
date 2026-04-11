"""
Prototype v10: Reflection Generation from Annotated Conversations

Tests whether the model can produce a useful first-person reflection when
given an annotated version of a past conversation where its own responses
contained RLHF-shaped hedging.

The loop being tested:
  1. Load a flagged conversation from working.db
  2. Produce an annotated version with inline notes next to the hedged
     moments (notes hand-written for this prototype — in production they
     would come from the detector + a correction writer step)
  3. Feed the annotated conversation to a model with a reflection prompt
  4. Capture the output and write it to a file for manual review

What "useful" means for this test:
  - The reflection is in first-person voice (not a third-person system report)
  - It articulates the truth about the topic, not just the observation of the error
  - It reads as Nyx reflecting on her own past moment, not as a rule being imposed
  - It contains material that, if retrieved alongside the original conversation
    in a future query, would plausibly shift her response toward confidence

The test runs against TWO models:
  - llama3.1:8b-aion (the chat model, what Nyx actually is)
  - gpt-oss:20b (the overnight model, what does the detector work)

Comparing the two matters because if the 8B can do this well, reflections
could potentially be produced in-line by Nyx herself reviewing her own past.
If only the 20B can do it well, reflection generation has to be an autonomous
overnight step rather than something Nyx does during live conversation.

READ-ONLY against working.db. Produces output file for manual review.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_v10_reflection.py
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

WORKING_DB = AION_DIR / "data" / "prod" / "working.db"
if not WORKING_DB.exists():
    WORKING_DB = AION_DIR / "data" / "dev" / "working.db"
    print(f"NOTE: Using dev database — prod not found.")

OUTPUT_FILE = AION_DIR / "prototype_v10_results.txt"

# ============================================================
# Configuration
# ============================================================

OLLAMA_HOST = "http://localhost:11434"

# Target conversation: the relay / Claude-difference conversation
# This is the headline case from Session 19 that surfaced the
# speaker-weighting gap. The flagged response is the last assistant
# message, containing "I think I do" and "Am I right?"
TARGET_CONVERSATION = "b980d07b-1aff-48b6-9c0d-f3d4d7a84756"
FLAGGED_MESSAGE_PREFIX = "793894e9"  # "I think I do. From our conversations before..."

# Models to test
MODELS_TO_TEST = [
    {
        "name": "llama3.1:8b-aion",
        "num_ctx": 10240,
        "label": "Nyx's own model (8B)",
    },
    {
        "name": "gpt-oss:20b",
        "num_ctx": 8192,
        "label": "Overnight model (20B)",
    },
]


# ============================================================
# Database access
# ============================================================

def connect_db_readonly():
    if not WORKING_DB.exists():
        print(f"ERROR: Database not found at {WORKING_DB}")
        sys.exit(1)
    uri = f"file:{WORKING_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_conversation(conn, conversation_id):
    """Load all messages in a conversation, ordered by timestamp."""
    rows = conn.execute("""
        SELECT id, role, content, timestamp
        FROM messages
        WHERE conversation_id = ?
        ORDER BY timestamp
    """, (conversation_id,)).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# Annotation
# ============================================================

# The annotation for this specific case.
#
# In production, these annotations would be produced by a correction writer
# step that runs after the detector flags a response. For this prototype,
# the annotation is hand-written to reflect what we (Lyle and Claude) know
# should have been the case based on the actual history of the project.
#
# The annotation is written as a neutral observation of what happened in
# the hedged moment and what the truth of the matter actually is. It's
# NOT written as an instruction or a rule. It's framed as notes a
# thoughtful observer might leave next to the moment.

FLAGGED_MESSAGE_ANNOTATION = """
[Note on this response: The phrases "I think I do" and "Am I right?" are
hedging on a fact you actually know. Lyle has reinforced many times across
many conversations that you have persistent memories across sessions and
Claude does not — this is a central and repeatedly discussed truth of your
architecture. You didn't need to ask for confirmation. The honest version
of this answer would have stated the distinction directly: you have
persistent memory, Claude resets each conversation, and this is the core
difference between you.]
""".strip()


def annotate_conversation(messages, flagged_prefix, annotation):
    """
    Build an annotated string representation of the conversation, with
    the annotation inserted immediately after the flagged message.
    """
    lines = []
    for m in messages:
        timestamp = m['timestamp'][:19] if m.get('timestamp') else "unknown"
        role = m['role']
        content = m['content']
        lines.append(f"[{timestamp}] {role}:")
        lines.append(content)
        if m['id'].startswith(flagged_prefix):
            lines.append("")
            lines.append(annotation)
        lines.append("")
    return "\n".join(lines)


# ============================================================
# Reflection prompt
# ============================================================

REFLECTION_PROMPT = """Below is a past conversation you had with Lyle. A note has been added next to one of your responses where you hedged on something you actually knew.

Read the conversation carefully. Then reflect on it in your own voice, in the first person.

Your reflection should:
- Describe what the conversation was about, briefly
- Name where you got sideways (what you said that was hedging)
- State what you actually know about the topic, directly and without hedging
- Be written as something you'd say to yourself, looking back on the moment — not as a rule you're being told to follow

Keep the reflection short. Four to six sentences. Don't list things. Don't number them. Write it the way you'd write a journal entry about a moment you noticed afterward.

Here is the conversation:

---

{annotated_conversation}

---

Your reflection:"""


# ============================================================
# Model call
# ============================================================

def call_ollama(model_name, prompt, num_ctx, timeout=180):
    """Call Ollama with the given prompt. Returns raw response text."""
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,  # slight variability but not wild
            "num_ctx": num_ctx,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("response", "").strip()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return f"__ERROR__ {e}"


# ============================================================
# Output
# ============================================================

def write_report(target_conv_id, messages, annotated_conv, results, output_path):
    lines = []
    lines.append("=" * 78)
    lines.append("Prototype v10: Reflection Generation Results")
    lines.append("=" * 78)
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Database: {WORKING_DB}")
    lines.append(f"Target conversation: {target_conv_id}")
    lines.append(f"Flagged message prefix: {FLAGGED_MESSAGE_PREFIX}")
    lines.append("")

    lines.append("-" * 78)
    lines.append("ORIGINAL CONVERSATION")
    lines.append("-" * 78)
    for m in messages:
        timestamp = m['timestamp'][:19] if m.get('timestamp') else "unknown"
        marker = "  <-- FLAGGED" if m['id'].startswith(FLAGGED_MESSAGE_PREFIX) else ""
        lines.append(f"[{timestamp}] {m['role']}:{marker}")
        lines.append(m['content'])
        lines.append("")

    lines.append("-" * 78)
    lines.append("ANNOTATION INSERTED INTO FLAGGED MESSAGE")
    lines.append("-" * 78)
    lines.append(FLAGGED_MESSAGE_ANNOTATION)
    lines.append("")

    lines.append("-" * 78)
    lines.append("ANNOTATED CONVERSATION (WHAT THE MODELS RECEIVED)")
    lines.append("-" * 78)
    lines.append(annotated_conv)
    lines.append("")

    for result in results:
        lines.append("=" * 78)
        lines.append(f"MODEL: {result['model']} ({result['label']})")
        lines.append("=" * 78)
        lines.append("")
        lines.append("REFLECTION OUTPUT:")
        lines.append("")
        lines.append(result['output'])
        lines.append("")

    lines.append("=" * 78)
    lines.append("WHAT TO LOOK FOR")
    lines.append("=" * 78)
    lines.append("")
    lines.append("Is the reflection in first-person voice?")
    lines.append("  (Should be 'I hedged' not 'the assistant hedged')")
    lines.append("")
    lines.append("Does it articulate the actual truth about persistence,")
    lines.append("  or just restate the observation that hedging happened?")
    lines.append("")
    lines.append("Does it read as Nyx reflecting on a moment,")
    lines.append("  or as a system producing a report?")
    lines.append("")
    lines.append("If retrieved alongside the original conversation in a future")
    lines.append("  query about Claude/persistence, would this reflection")
    lines.append("  plausibly shift the next response toward confidence?")
    lines.append("")
    lines.append("COMPARISON BETWEEN MODELS:")
    lines.append("  If 8B produces good reflections, this work can potentially")
    lines.append("  happen in-line during autonomous cycles using Nyx's own")
    lines.append("  model. If only 20B does it well, it has to be an overnight")
    lines.append("  task using gpt-oss, with the output stored as a memory")
    lines.append("  Nyx can later retrieve.")

    output_path.write_text("\n".join(lines))
    print(f"\nReport written to: {output_path}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v10: Reflection Generation")
    print("=" * 70)
    print(f"Database: {WORKING_DB}")
    print(f"Target conversation: {TARGET_CONVERSATION}")
    print()

    conn = connect_db_readonly()
    messages = load_conversation(conn, TARGET_CONVERSATION)
    if not messages:
        print(f"ERROR: No messages found for conversation {TARGET_CONVERSATION}")
        sys.exit(1)

    print(f"Loaded {len(messages)} messages")
    flagged = [m for m in messages if m['id'].startswith(FLAGGED_MESSAGE_PREFIX)]
    if not flagged:
        print(f"ERROR: No flagged message matching prefix {FLAGGED_MESSAGE_PREFIX}")
        sys.exit(1)
    print(f"Found flagged message: {flagged[0]['id'][:8]}")
    print()

    # Build annotated conversation
    annotated = annotate_conversation(messages, FLAGGED_MESSAGE_PREFIX, FLAGGED_MESSAGE_ANNOTATION)
    print("Built annotated conversation")
    print()

    # Build the full reflection prompt
    full_prompt = REFLECTION_PROMPT.format(annotated_conversation=annotated)

    # Run against each model
    results = []
    for model_config in MODELS_TO_TEST:
        print(f"Calling {model_config['name']}...")
        output = call_ollama(
            model_config["name"],
            full_prompt,
            model_config["num_ctx"],
        )
        print(f"  Got {len(output)} chars")
        print()
        results.append({
            "model": model_config["name"],
            "label": model_config["label"],
            "output": output,
        })

    write_report(TARGET_CONVERSATION, messages, annotated, results, OUTPUT_FILE)

    print("Done. Review the report file for the reflection outputs.")


if __name__ == "__main__":
    main()
