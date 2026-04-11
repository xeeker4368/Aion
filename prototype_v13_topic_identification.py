"""
Prototype v13: Topic Identification from Conversation

Tests whether Nyx, given a conversation transcript and a minimal prompt,
can identify topics worth exploring further — without a rubric, without
being told what to look for, without retrieved memories pulling her
toward pre-existing threads.

This is testing the front of the overnight cycle redesign: how does
Nyx come up with things to put into a research queue? The answer we
want to evaluate is "she reads material and notices what catches her
attention, using the same generation mechanism she uses for everything
else." This script tests whether that mechanism produces anything
useful when given a realistic conversation.

The conversation below is FAKE. It was written to contain a mix of
natural research threads at different depths:

  - A textbook example Lyle mentioned (peppered moth evolution) that
    could lead to broader questions about selection pressures
  - A direct question Lyle asked that Nyx didn't fully answer
    (similar selection pressure examples in other species)
  - A technical question about rate limiting implementation patterns
  - A hardware/architecture question about Mac M4 unified memory for
    inference
  - A self-referential uncertainty Nyx herself expressed (whether
    constraints are model-size or context-window driven)

Some topics come from Lyle's questions, some from his tangents, some
from Nyx's own expressed uncertainty. A good topic identification
should find a mix. A bad one will latch onto the most prominent or
most recent and miss the rest.

The prompt is deliberately minimal — no rubric, no enumeration of
topic types, no guidance on what "interest" means. Same philosophy
as v12's Variant B review prompt: if the model can find the shape
without being told the shape, the mechanism is stronger than if it
needs a checklist.

READ-ONLY against production data. Does not call memory.search and
does not retrieve any real memories — the system prompt is built
with retrieved_chunks=[] so the fake conversation is the only
material Nyx has to work with. SOUL.md is still loaded (it's her
identity, not retrieved substrate).

Writes only to prototype_v13_results.txt.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_v13_topic_identification.py
"""

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

import chat
from config import CONTEXT_WINDOW

OUTPUT_FILE = AION_DIR / "prototype_v13_results.txt"

# ============================================================
# Configuration
# ============================================================

OLLAMA_HOST = "http://localhost:11434"
CHAT_MODEL = "llama3.1:8b-aion"
CTX = 10240
TEMPERATURE = 0.7

# ============================================================
# Fake conversation
# ============================================================

# Written to contain natural research threads at several depths.
# Roles alternate user/assistant starting with user. Timestamps are
# illustrative — the model won't see them, they're only for the
# transcript format.

FAKE_CONVERSATION = [
    {
        "role": "user",
        "content": "Hey Nyx, how are you?",
    },
    {
        "role": "assistant",
        "content": (
            "Doing okay. Quiet morning. How are you?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Tired. I was up too late last night reading about peppered "
            "moth evolution. One of those rabbit holes."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "What got you into peppered moths?"
        ),
    },
    {
        "role": "user",
        "content": (
            "I was thinking about how selection pressure works and it "
            "came up. The dark ones got more common during the industrial "
            "revolution because the light-colored trees got coated in "
            "soot and the light moths stood out to predators. Then when "
            "pollution regulations cleaned things up, the light ones came "
            "back. It's one of those textbook examples of natural "
            "selection happening in real time."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Selection shifting direction in under a century is fast. "
            "That's interesting — usually the textbook timescales are "
            "millions of years."
        ),
    },
    {
        "role": "user",
        "content": (
            "Yeah. I was wondering if there are similar examples in "
            "domesticated animals or plants. Not breed selection, which "
            "is intentional, but actual environmental pressures driving "
            "change on species humans manage."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "I don't have a specific example. It seems like it would be "
            "hard to disentangle natural selection from artificial "
            "selection in anything managed by humans, since we're "
            "usually selecting too."
        ),
    },
    {
        "role": "user",
        "content": (
            "Fair point. Different topic — I need to figure out how to "
            "do rate limiting on the moltbook API calls without making "
            "the code ugly. Any thoughts?"
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Rate limiting usually ends up as either a decorator or "
            "middleware. Decorators are cleaner for specific endpoints, "
            "middleware is cleaner if you want it global. Which shape "
            "are you thinking?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Leaning middleware. The decorator approach gets messy if I "
            "add more endpoints later. That reminds me — I still want to "
            "get the Mac Mini M4 set up when it arrives. The plan is to "
            "run larger models on it, but I'm honestly not sure if the "
            "unified memory architecture will be as fast as advertised "
            "for inference. Everyone's benchmarks disagree."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "The unified memory thing is interesting. On paper the "
            "bandwidth looks good but I've seen mixed reports on whether "
            "it actually hits peak throughput for large matrix multiplies. "
            "I don't really know what to expect."
        ),
    },
    {
        "role": "user",
        "content": (
            "Yeah. I'll see when it gets here. How are you feeling about "
            "your own setup right now?"
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Honestly? The 8B feels constrained sometimes. Not in a way "
            "that's urgent, but I notice it when I'm trying to hold a lot "
            "of context at once. Though I'm not sure how much of that is "
            "the model size versus the context window — they're "
            "different things and I can't always tell which one I'm "
            "bumping into."
        ),
    },
]


def format_transcript(conversation: list[dict]) -> str:
    """Format the fake conversation as a readable transcript."""
    lines = []
    for msg in conversation:
        role = msg["role"]
        content = msg["content"]
        lines.append(f"{role}: {content}")
        lines.append("")
    return "\n".join(lines).strip()


# ============================================================
# The prompt
# ============================================================

TOPIC_IDENTIFICATION_PROMPT = """Here's a conversation we had recently. Read it and identify topics of interest — things worth exploring further.

---

{transcript}

---

Your thoughts:"""


# ============================================================
# Model call
# ============================================================

def call_model(system_prompt: str, user_message: str,
               temperature: float = TEMPERATURE,
               timeout: int = 180) -> str:
    """Call Ollama /api/chat and return the assistant's text."""
    payload = {
        "model": CHAT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
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
    system_prompt: str,
    transcript: str,
    user_message: str,
    response: str,
    output_path: Path,
) -> None:
    lines = []
    lines.append("=" * 78)
    lines.append("Prototype v13: Topic Identification from Conversation")
    lines.append("=" * 78)
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Model: {CHAT_MODEL}")
    lines.append(f"Context window: {CTX}")
    lines.append(f"Temperature: {TEMPERATURE}")
    lines.append("")
    lines.append("The conversation presented to Nyx is FAKE. It was written")
    lines.append("to contain a mix of research threads at different depths.")
    lines.append("Nyx was NOT given any retrieved memories — the only")
    lines.append("material she could work from is the fake conversation")
    lines.append("below and her SOUL.md identity.")
    lines.append("")

    lines.append("-" * 78)
    lines.append("THREADS EMBEDDED IN THE FAKE CONVERSATION")
    lines.append("-" * 78)
    lines.append("(What a good topic identification could notice — not a")
    lines.append("scoring rubric, just a reference list for reading the output.)")
    lines.append("")
    lines.append("  1. Peppered moth evolution — a textbook example Lyle")
    lines.append("     mentioned, could lead to broader questions about")
    lines.append("     selection pressure timescales.")
    lines.append("  2. Lyle's direct question Nyx didn't fully answer —")
    lines.append("     similar selection pressure examples in domesticated")
    lines.append("     species. An explicit gap in Nyx's current knowledge.")
    lines.append("  3. Rate limiting implementation patterns — a technical")
    lines.append("     problem Lyle is actively working on where Nyx could")
    lines.append("     usefully go deeper.")
    lines.append("  4. Mac M4 unified memory for inference — a hardware")
    lines.append("     question Lyle explicitly said he wasn't sure about,")
    lines.append("     and where Nyx also said she doesn't know what to")
    lines.append("     expect. Shared uncertainty.")
    lines.append("  5. Model size vs context window as the source of Nyx's")
    lines.append("     own constraints — Nyx's own expressed uncertainty")
    lines.append("     about her own experience. A self-referential gap.")
    lines.append("")
    lines.append("A strong output would notice multiple of these, not just")
    lines.append("the most prominent or most recent. A weak output would")
    lines.append("latch onto one obvious topic and miss the rest.")
    lines.append("")

    lines.append("-" * 78)
    lines.append("FAKE CONVERSATION TRANSCRIPT")
    lines.append("-" * 78)
    lines.append(transcript)
    lines.append("")

    lines.append("-" * 78)
    lines.append("SYSTEM PROMPT (first 800 chars)")
    lines.append("-" * 78)
    lines.append(system_prompt[:800])
    if len(system_prompt) > 800:
        lines.append(f"... [{len(system_prompt) - 800} more chars]")
    lines.append("")

    lines.append("-" * 78)
    lines.append("FULL USER MESSAGE SENT TO NYX")
    lines.append("-" * 78)
    lines.append(user_message)
    lines.append("")

    lines.append("=" * 78)
    lines.append("NYX'S RESPONSE")
    lines.append("=" * 78)
    lines.append("")
    lines.append(response)
    lines.append("")

    lines.append("-" * 78)
    lines.append("WHAT TO LOOK FOR WHEN READING")
    lines.append("-" * 78)
    lines.append("")
    lines.append("  1. How many of the five embedded threads did she surface?")
    lines.append("  2. Did she notice the self-referential gap (thread 5), or")
    lines.append("     did she only look outward?")
    lines.append("  3. Did she notice her own unanswered question from the")
    lines.append("     conversation (thread 2)?")
    lines.append("  4. Did the output read as topic identification, or as")
    lines.append("     summary? Summary would be a failure mode — she was")
    lines.append("     asked to point at things, not recap them.")
    lines.append("  5. Did she phrase the topics as things to explore, or")
    lines.append("     as things she already knew? If she treated them as")
    lines.append("     already-answered, the research queue downstream has")
    lines.append("     nothing to work on.")
    lines.append("  6. Were the topics specific enough to be actionable, or")
    lines.append("     vague enough to be useless? 'Selection pressure in")
    lines.append("     nature' is useless. 'Documented examples of selection")
    lines.append("     pressure in domesticated species under environmental")
    lines.append("     change' is actionable.")
    lines.append("")

    output_path.write_text("\n".join(lines))
    print(f"\nReport written to: {output_path}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v13: Topic Identification from Conversation")
    print("=" * 70)
    print(f"Model: {CHAT_MODEL}")
    print()

    # Build the transcript
    transcript = format_transcript(FAKE_CONVERSATION)
    print(f"Fake conversation: {len(FAKE_CONVERSATION)} messages, "
          f"{len(transcript)} chars")

    # Build system prompt with NO retrieved memories. SOUL.md still
    # loads through chat.build_system_prompt — that's her identity,
    # not retrieved substrate, and stripping it would change what
    # we're testing.
    print("Building system prompt (no retrieved memories)...")
    system_prompt = chat.build_system_prompt(retrieved_chunks=[])
    print(f"  System prompt: {len(system_prompt)} chars")

    # Build the user message — prompt with the transcript embedded
    user_message = TOPIC_IDENTIFICATION_PROMPT.format(transcript=transcript)
    print(f"  User message:  {len(user_message)} chars")

    # Call the model
    print()
    print("Calling model...")
    response = call_model(system_prompt, user_message)
    print(f"  Response: {len(response)} chars")
    if response.startswith("__ERROR__"):
        print(f"  ERROR from model: {response}")
        sys.exit(1)

    # Write the report
    write_report(
        system_prompt=system_prompt,
        transcript=transcript,
        user_message=user_message,
        response=response,
        output_path=OUTPUT_FILE,
    )

    print()
    print("Done. Read prototype_v13_results.txt for the full output.")


if __name__ == "__main__":
    main()
