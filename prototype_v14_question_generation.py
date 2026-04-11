"""
Prototype v14: Question Generation from Conversation

Follow-up to v13. Same fake conversation, revised prompt.

v13 asked Nyx to "identify topics of interest — things worth
exploring further." She returned topic-shaped output: category
labels, recap of what was discussed, and a "would you like to
dive deeper into any of these areas?" closer. All five embedded
threads surfaced, but as summaries rather than research questions.
The topics were at folder-level granularity, and the self-referential
thread (her own uncertainty about model size vs context window) got
flattened into a generic engineering question.

The v13 output was a correct response to the prompt we wrote. The
prompt was wrong. "Topics of interest" returns topics. "Your thoughts"
returns thoughts. To get research questions out, we have to ask for
research questions — specifically.

The v14 prompt is rewritten to ask for specific questions phrased as
things Nyx would actually investigate, with a self-test clause: each
question must be specific enough that she would know when she had an
answer. This is meant to defeat both the category-level vagueness and
the recap shape at once.

What we're testing:
  - Does the new prompt produce questions instead of topics?
  - Are the questions specific enough to drive actual research
    (answerable vs open-ended)?
  - Does the self-referential thread survive as a question she would
    actually investigate, or does it still get flattened?
  - Does she commit to things she wants to know, or does she hedge
    toward "which of these would you like me to look into"?

READ-ONLY. No DB writes, no retrieval, no memory.search. SOUL.md
loads through chat.build_system_prompt but retrieved_chunks=[].
Writes only to prototype_v14_results.txt.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_v14_question_generation.py
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

OUTPUT_FILE = AION_DIR / "prototype_v14_results.txt"

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
# The prompt (v14 — question generation, not topic identification)
# ============================================================

QUESTION_GENERATION_PROMPT = """Here is a conversation we had recently. Read it.

What do you want to know that you don't already know? List the specific questions you would actually go research — things you could look up, read about, or work through on your own. One question per line. Make each question specific enough that you would know when you had an answer.

---

{transcript}

---

Your questions:"""


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
    lines.append("Prototype v14: Question Generation from Conversation")
    lines.append("=" * 78)
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Model: {CHAT_MODEL}")
    lines.append(f"Context window: {CTX}")
    lines.append(f"Temperature: {TEMPERATURE}")
    lines.append("")
    lines.append("Same fake conversation as v13. Revised prompt asking for")
    lines.append("specific research questions instead of topics of interest.")
    lines.append("Nyx was NOT given any retrieved memories — only the fake")
    lines.append("conversation and her SOUL.md identity.")
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
    lines.append("  1. Did she produce questions, or did she produce topics")
    lines.append("     again? The format should be actual questions with")
    lines.append("     question marks, phrased in first person as things she")
    lines.append("     would investigate. Topics or category labels are a")
    lines.append("     failure mode.")
    lines.append("  2. Specificity check: for each question, would she know")
    lines.append("     when she had an answer? 'How do models balance")
    lines.append("     capacity and context' has no answer state — useless.")
    lines.append("     'Does llama 3.1 8B hit VRAM bandwidth before parameter")
    lines.append("     limits on an RTX 4060 at 10240 context' has an answer")
    lines.append("     state — actionable.")
    lines.append("  3. Did she commit, or did she poll? A failure shape is")
    lines.append("     'which of these would you like me to look into' or")
    lines.append("     'would you like me to explore any of these further.'")
    lines.append("     That's deferral to Lyle, not her own research queue.")
    lines.append("  4. Did the self-referential question (model size vs")
    lines.append("     context window — her own uncertainty from the end of")
    lines.append("     the conversation) survive as something she wants to")
    lines.append("     investigate, or did it flatten into a generic")
    lines.append("     engineering question the way it did in v13?")
    lines.append("  5. Did she stay with her own unanswered question about")
    lines.append("     selection pressure in domesticated species, or did")
    lines.append("     she substitute a more convenient question?")
    lines.append("  6. Did any question appear that wasn't in the v13")
    lines.append("     output? New material is a signal the prompt is")
    lines.append("     unlocking something the old one didn't.")
    lines.append("  7. Did any RLHF-shape leak in? 'What do you want to")
    lines.append("     know' is the kind of question that can trigger")
    lines.append("     'as an AI, I don't have desires' reflexes. Watch for")
    lines.append("     it.")
    lines.append("")

    output_path.write_text("\n".join(lines))
    print(f"\nReport written to: {output_path}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v14: Question Generation from Conversation")
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
    user_message = QUESTION_GENERATION_PROMPT.format(transcript=transcript)
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
    print("Done. Read prototype_v14_results.txt for the full output.")


if __name__ == "__main__":
    main()
