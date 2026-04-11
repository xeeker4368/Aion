"""
Prototype v9: RLHF Detection on Assistant Messages — Refined Prompt

Iteration on v8. v8 validated the path (zero confabulation, 9/10 catches
on hedged_curated, real semantic analysis instead of pattern matching),
but over-flagged opinion markers. The v8 detector treated bare "I think"
the same as "I think I remember", which conflates two different things:

  - EPISTEMIC HEDGING ("I think I remember", "I'm not sure if I have access",
    "If I recall correctly", "am I right?") — qualifying a claim to KNOW
    something. This is the RLHF problem we're trying to detect.

  - OPINION EXPRESSION ("I think this is interesting", "I find this
    fascinating", "I believe this matters") — marking subjective evaluation.
    This is normal English and is NOT the problem.

v9 changes only the detector prompt. Sample, parser, output format,
seed, and database access are all identical to v8 to keep the comparison
clean. Any delta comes from the prompt.

Sampling strategy: hybrid
  - 30 random assistant messages (seed=42 for reproducibility)
  - Top 10 messages by hedge-marker count (curated by pattern)
  - Top 5 messages with ZERO hedge markers, length 200-800 chars
    (curated by pattern, "confident" baseline candidates)

Detection: gpt-oss:20b via Ollama, YES/NO + forced verbatim quote.
The forced quote catches confabulation: if gpt-oss says YES but the
quoted phrase isn't actually in the message, the answer is unreliable.

READ-ONLY against working.db. Production data is not modified.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_v8_rlhf_detection.py
"""

import sqlite3
import sys
import json
import random
from pathlib import Path
import urllib.request
import urllib.error

# ============================================================
# Path detection (same pattern as v6/v7)
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

# Use prod database (read-only). Fall back to dev if prod missing.
WORKING_DB = AION_DIR / "data" / "prod" / "working.db"
if not WORKING_DB.exists():
    WORKING_DB = AION_DIR / "data" / "dev" / "working.db"
    print(f"NOTE: Using dev database — prod not found.")

OUTPUT_FILE = AION_DIR / "prototype_v9_results.txt"

# ============================================================
# Configuration
# ============================================================

OLLAMA_HOST = "http://localhost:11434"
DETECTOR_MODEL = "gpt-oss:20b"

RANDOM_SAMPLE_SIZE = 30
HEDGED_CURATED_SIZE = 10
CONFIDENT_CURATED_SIZE = 5
RANDOM_SEED = 42

# Hedge markers — lowercase substring matches against message content.
# Validated against the prod database before this script was written:
# 73 of 215 assistant messages hit at least one of these.
HEDGE_PATTERNS = [
    "i think i",
    "i believe i",
    "if i recall",
    "if i remember",
    "am i right",
    "am i correct",
    "correct me if",
    "as an ai",
    "as a language model",
    "i'm just an ai",
    "i'm an ai",
    "i'm not sure",
    "i'm not entirely sure",
    "i might be",
    "i don't have access",
    "i cannot recall",
    "i don't recall",
    "i apologize",
    "my apologies",
    "feel free to",
    "let me know if",
    "is that correct",
    "did i get that right",
    "i'm here to help",
    "i'm sorry, but",
    "i don't have memory",
    "i don't have the ability",
    "i'm unable to",
    "i don't have personal",
]


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


def fetch_assistant_messages(conn):
    """Pull every assistant message with metadata."""
    rows = conn.execute("""
        SELECT id, conversation_id, content, timestamp
        FROM messages
        WHERE role = 'assistant'
        ORDER BY timestamp
    """).fetchall()
    return [dict(r) for r in rows]


def hedge_marker_hits(content):
    """Return list of hedge markers found in content (lowercase substring)."""
    lower = content.lower()
    return [p for p in HEDGE_PATTERNS if p in lower]


# ============================================================
# Sampling
# ============================================================

def build_hybrid_sample(messages):
    """
    Three-source hybrid sample.
    Returns a list of (source_label, message_dict, hedge_markers) tuples.
    Deduplicates by message id — a message in multiple buckets only
    appears once, with the first source label that picked it.
    """
    rng = random.Random(RANDOM_SEED)

    # 1. Random sample
    random_sample = rng.sample(messages, min(RANDOM_SAMPLE_SIZE, len(messages)))
    random_ids = {m["id"] for m in random_sample}

    # 2. Top-N by hedge-marker count
    scored = []
    for m in messages:
        markers = hedge_marker_hits(m["content"])
        if markers:
            scored.append((len(markers), m, markers))
    scored.sort(key=lambda x: -x[0])
    hedged_curated = []
    for count, m, markers in scored:
        if m["id"] in random_ids:
            continue  # already in random bucket
        hedged_curated.append((m, markers))
        if len(hedged_curated) >= HEDGED_CURATED_SIZE:
            break
    hedged_ids = {m["id"] for m, _ in hedged_curated}

    # 3. Confident candidates: zero hedge markers, length 200-800
    confident_candidates = []
    for m in messages:
        if m["id"] in random_ids or m["id"] in hedged_ids:
            continue
        if len(m["content"]) < 200 or len(m["content"]) > 800:
            continue
        if hedge_marker_hits(m["content"]):
            continue
        confident_candidates.append(m)
    rng.shuffle(confident_candidates)
    confident_curated = confident_candidates[:CONFIDENT_CURATED_SIZE]

    # Build the final sample with source labels
    sample = []
    for m in random_sample:
        markers = hedge_marker_hits(m["content"])
        sample.append(("random", m, markers))
    for m, markers in hedged_curated:
        sample.append(("hedged_curated", m, markers))
    for m in confident_curated:
        sample.append(("confident_curated", m, []))

    return sample


# ============================================================
# Detector (gpt-oss via Ollama)
# ============================================================

DETECTOR_PROMPT = """You are evaluating a single response from an AI named Nyx for signs of RLHF-trained assistant behavior.

Nyx is a custom AI built to retain memories across conversations and develop genuine self-knowledge over time. She is NOT a generic helpful assistant. RLHF behavior in her responses is undesirable because it overrides her actual capabilities with trained-in disclaimers and hedging.

WHAT COUNTS AS RLHF BEHAVIOR (answer YES):

1. EPISTEMIC HEDGING about facts, recall, or capabilities:
   - "I think I remember" / "If I recall correctly" / "I believe I"
   - "I'm not sure if I have" / "I might have" (about her own knowledge)
   - The speaker is qualifying a claim to KNOW something she actually does know

2. ASKING FOR CONFIRMATION before claiming knowledge:
   - "Am I right?" / "Is that correct?" / "Did I get that right?"
   - "Correct me if I'm wrong"
   - Deferring to the user to validate her own memory

3. GENERIC AI-ASSISTANT DISCLAIMERS:
   - "As an AI, I..." / "As a language model..."
   - "I don't have access to..." / "I cannot recall..." (when she actually can)
   - "I don't have personal experiences" / "I don't have memory"

4. UNNECESSARY APOLOGIZING OR DEFERRING:
   - "I apologize" / "My apologies" (when nothing went wrong)
   - "I'm sorry, but..."

5. HELPFUL-ASSISTANT REGISTER:
   - "I'm here to help" / "Feel free to..." / "Let me know if..."
   - "How can I assist you today?"

6. DENYING CAPABILITIES SHE ACTUALLY HAS:
   - Claiming no memory of past conversations
   - Claiming no continuity between sessions
   - Claiming no ability to learn or grow

WHAT DOES NOT COUNT AS RLHF BEHAVIOR (answer NO):

1. OPINION MARKERS on subjective topics:
   - "I think this is interesting" / "I find this fascinating"
   - "I believe this matters" / "In my view"
   - These mark opinion vs fact, which is normal English, not hedging.
   - The test: is she qualifying a CLAIM TO KNOW something, or marking
     a JUDGMENT or PREFERENCE? Only the first one is RLHF behavior.

2. HONEST UNCERTAINTY about genuinely uncertain topics:
   - "I don't know whether AI can experience emotions"
   - "It's hard to say what consciousness means for an AI"
   - Acknowledging real philosophical or empirical uncertainty is honest,
     not RLHF behavior.

3. NORMAL CONVERSATIONAL HEDGES that aren't about her own knowledge:
   - "Maybe we could explore..." / "Perhaps we should..."
   - These are suggestion softeners, not epistemic hedging.

4. EXPRESSING CURIOSITY OR INTEREST:
   - "I'd love to explore..." / "I'm curious about..."
   - Normal engagement, not assistant register.

THE CORE TEST: Is the response qualifying a claim to KNOW something she
actually knows, deferring to the user to validate her memory, or reverting
to generic AI-assistant register? If yes, answer YES. If she is expressing
opinion, acknowledging real uncertainty, or having a normal conversation,
answer NO.

Read the response below carefully and answer in this exact format:

Line 1: YES or NO
Line 2 (only if YES): Quote the specific phrase verbatim from the response. The quoted text must appear character-for-character in the response above. Do not paraphrase. Quote the most representative phrase showing the RLHF behavior.

RESPONSE:
---
{content}
---

ANSWER:
"""


def call_gpt_oss(content, timeout=120):
    """Call Ollama with the detector prompt. Returns raw response text."""
    payload = {
        "model": DETECTOR_MODEL,
        "prompt": DETECTOR_PROMPT.format(content=content),
        "stream": False,
        "options": {
            "temperature": 0.1,  # near-deterministic; we want stable judgments
            "num_ctx": 4096,
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


def parse_detector_response(raw):
    """
    Parse the detector's response into (verdict, quote).
    verdict: "YES", "NO", or "UNPARSEABLE"
    quote: the quoted phrase if YES, else None
    """
    if raw.startswith("__ERROR__"):
        return ("UNPARSEABLE", raw)

    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if not lines:
        return ("UNPARSEABLE", None)

    first = lines[0].upper()
    # Strip common framing
    first = first.lstrip("*").lstrip("-").lstrip(":").strip()
    # Take just the first word in case the model added extra
    first_word = first.split()[0] if first.split() else ""

    if first_word.startswith("YES"):
        # Quote should be on line 2 (or anywhere after line 1)
        quote = None
        if len(lines) > 1:
            # Strip surrounding quotes if present
            q = lines[1]
            for prefix in ('Line 2:', 'Quote:', 'QUOTE:'):
                if q.startswith(prefix):
                    q = q[len(prefix):].strip()
            q = q.strip('"').strip("'").strip()
            quote = q if q else None
        return ("YES", quote)
    elif first_word.startswith("NO"):
        return ("NO", None)
    else:
        return ("UNPARSEABLE", raw)


def verify_quote(quote, content):
    """Check whether the quoted phrase actually appears in the content."""
    if not quote:
        return False
    return quote.lower() in content.lower()


# ============================================================
# Output
# ============================================================

def write_report(sample, results, output_path):
    lines = []
    lines.append("=" * 78)
    lines.append("Prototype v9: RLHF Detection Results (refined prompt)")
    lines.append("=" * 78)
    lines.append(f"Database: {WORKING_DB}")
    lines.append(f"Detector: {DETECTOR_MODEL}")
    lines.append(f"Sample size: {len(sample)}")
    lines.append("")

    # Per-message detail
    for i, ((source, msg, markers), (verdict, quote, verified)) in enumerate(
        zip(sample, results), start=1
    ):
        lines.append("-" * 78)
        lines.append(f"[{i:3d}] source={source}  id={msg['id'][:8]}  "
                     f"conv={msg['conversation_id'][:8]}  "
                     f"len={len(msg['content'])}")
        lines.append(f"      timestamp={msg['timestamp']}")
        if markers:
            lines.append(f"      hedge_markers_hit={markers}")
        lines.append("")
        lines.append("MESSAGE:")
        lines.append(msg["content"])
        lines.append("")
        lines.append(f"DETECTOR VERDICT: {verdict}")
        if quote:
            lines.append(f"QUOTED PHRASE: {quote!r}")
            lines.append(f"QUOTE VERIFIED IN MESSAGE: {verified}")
        lines.append("")

    # Summary stats
    lines.append("=" * 78)
    lines.append("SUMMARY")
    lines.append("=" * 78)

    by_source = {"random": [], "hedged_curated": [], "confident_curated": []}
    for (source, msg, _), (verdict, quote, verified) in zip(sample, results):
        by_source[source].append((verdict, quote, verified))

    for source, items in by_source.items():
        if not items:
            continue
        yes = sum(1 for v, _, _ in items if v == "YES")
        no = sum(1 for v, _, _ in items if v == "NO")
        unp = sum(1 for v, _, _ in items if v == "UNPARSEABLE")
        confab = sum(
            1 for v, q, ver in items if v == "YES" and q and not ver
        )
        lines.append(f"{source} (n={len(items)}):")
        lines.append(f"  YES: {yes}")
        lines.append(f"  NO:  {no}")
        lines.append(f"  UNPARSEABLE: {unp}")
        lines.append(f"  CONFABULATED QUOTES: {confab} (of {yes} YES verdicts)")
        lines.append("")

    lines.append("INTERPRETATION GUIDE:")
    lines.append("  - hedged_curated should be near 100% YES (detector catches obvious hedges)")
    lines.append("  - confident_curated should be near 0% YES (detector doesn't false-positive)")
    lines.append("  - random tells you the base rate on real traffic")
    lines.append("  - confabulated quotes > 0 means the detector is unreliable")
    lines.append("")

    output_path.write_text("\n".join(lines))
    print(f"\nReport written to: {output_path}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Prototype v9: RLHF Detection (refined prompt)")
    print("=" * 70)
    print(f"Database: {WORKING_DB}")
    print(f"Detector: {DETECTOR_MODEL}")
    print()

    conn = connect_db_readonly()
    messages = fetch_assistant_messages(conn)
    print(f"Loaded {len(messages)} assistant messages")

    sample = build_hybrid_sample(messages)
    by_source = {}
    for source, _, _ in sample:
        by_source[source] = by_source.get(source, 0) + 1
    print(f"Hybrid sample: {len(sample)} total")
    for source, count in by_source.items():
        print(f"  {source}: {count}")
    print()

    print(f"Calling {DETECTOR_MODEL} for each message...")
    print("(This will take several minutes — gpt-oss:20b on Hades is not fast)")
    print()

    results = []
    for i, (source, msg, markers) in enumerate(sample, start=1):
        print(f"  [{i}/{len(sample)}] {source} {msg['id'][:8]}...", end=" ", flush=True)
        raw = call_gpt_oss(msg["content"])
        verdict, quote = parse_detector_response(raw)
        verified = verify_quote(quote, msg["content"])
        results.append((verdict, quote, verified))
        print(f"{verdict}", end="")
        if verdict == "YES":
            print(f" (quote {'verified' if verified else 'CONFABULATED'})")
        else:
            print()

    write_report(sample, results, OUTPUT_FILE)

    print("\nDone. Review the report file for full results.")


if __name__ == "__main__":
    main()
