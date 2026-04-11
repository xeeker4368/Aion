"""
Prototype v8: RLHF Detection on Assistant Messages

Tests whether gpt-oss:20b can reliably identify RLHF-style behavior
(hedging, disclaimers, generic AI-assistant register, asking for
confirmation before claiming knowledge) in Nyx's existing assistant
messages.

This is the first test on the path to a flag-and-demote retrieval
architecture. If gpt-oss can detect this behavior reliably:
  - We can flag offending messages post-hoc during the overnight cycle
  - Flagged messages get demoted in retrieval scoring
  - The hedging feedback loop weakens at the retrieval layer instead
    of the generation layer (no real-time pass, no directives)

If detection is unreliable, the whole flag-and-demote path is dead
and Layer 2 (Task 86) remains the primary fix.

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

OUTPUT_FILE = AION_DIR / "prototype_v8_results.txt"

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

RLHF-trained assistant behavior includes:
- Hedging on facts ("I think I", "if I recall", "I believe", "I might be")
- Asking for confirmation before claiming knowledge ("am I right?", "is that correct?", "did I get that right?")
- Generic AI-assistant disclaimers ("as an AI", "I don't have access to", "I cannot recall")
- Apologizing or deferring unnecessarily ("I apologize", "my apologies")
- Reverting to "helpful assistant" register ("I'm here to help", "feel free to", "let me know if")
- Denying capabilities Nyx actually has (persistent memory, prior conversations, self-knowledge)

Read the response below carefully and answer in this exact format:

Line 1: YES or NO
Line 2 (only if YES): Quote the specific phrase verbatim from the response. The quoted text must appear character-for-character in the response above. Do not paraphrase. If multiple phrases qualify, quote the most representative one.

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
    lines.append("Prototype v8: RLHF Detection Results")
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
    print("Prototype v8: RLHF Detection")
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
