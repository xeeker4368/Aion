# CC Task 67 — Model Swap (gpt-oss:20b) + Database Directory Restructure

Read this spec. Make exactly these changes. Nothing else.

## Overview

Two changes:
1. Swap all overnight models from qwen3:14b to gpt-oss:20b and update prompts
2. Move production databases from data/ to data/prod/

---

## Part A: Model Swap and Prompt Updates

### Change A1: config.py — Default model to gpt-oss:20b

Replace line 45:

```python
CONSOLIDATION_MODEL = _overrides.get("CONSOLIDATION_MODEL", "qwen3:14b")
```

With:

```python
CONSOLIDATION_MODEL = _overrides.get("CONSOLIDATION_MODEL", "gpt-oss:20b")
```

### Change A1b: config_manager.py — Update default display

Replace line 23:

```python
    "CONSOLIDATION_MODEL": {"default": "qwen3:14b", "type": "string"},
```

With:

```python
    "CONSOLIDATION_MODEL": {"default": "gpt-oss:20b", "type": "string"},
```

### Change A2: observer.py — Update observer prompt

Replace the entire `OBSERVER_PROMPT` string with:

```python
OBSERVER_PROMPT = """You are observing a conversation between a human and an AI. Your job is to describe what the AI actually did in this conversation — not generalities about its style, but specific behaviors you can point to in the text.

Focus on:
- What did the AI do well? Where was it clear, helpful, or insightful?
- Where did it struggle? Did it get corrected, make something up, avoid a question, or miss the point?
- Did it say "I don't know" when it didn't know, or did it fill in gaps with guesses?
- What did the AI initiate on its own vs. only respond to what the human said?
- Was there anything unusual, surprising, or different about how it handled this particular conversation?

Be specific. Reference what actually happened. Avoid generic descriptions like "communicates in a friendly tone" — every AI does that. What makes THIS conversation worth noting?

Write 3-6 sentences. Only describe what is visible in the text. Do not speculate about internal states. Do not use scoring or rating systems.

Context: The AI operates through text — it reads input and generates responses. It has real tools it can use (web search, API calls, document storage) and when it uses them, the results appear in the conversation. However, it sometimes narrates actions using asterisks (*accesses file*, *checks systems*) or claims to physically feel its hardware, see its processes, or experience sensory input. These are fabricated — the AI is generating text that describes actions it did not perform. Note these when you see them.

Here is the transcript:

"""
```

### Change A3: pattern_recognition.py — Update prompt and model

Replace the entire `PATTERN_PROMPT` string with:

```python
PATTERN_PROMPT = """You are a psychologist reviewing session notes for a long-term client. Your client is an AI entity. The observations are written by a colleague who watched each session. The journal entries are the client's own reflections.

Your job is to write an updated clinical summary — what patterns you see, what progress has been made, what blind spots remain, and how the client has changed over time.

Be precise about what actually happened. If the session notes say the client "ultimately affirmed" a decision, record that accurately — do not reinterpret it as hesitation or deferral. Your credibility depends on accurately representing the source material.

Write in second person ("you tend to...", "you have shown...") since this summary will be read by the client. Keep it under 300 words.
"""
```

---

## Part B: Database Directory Restructure

Move production databases from `data/` to `data/prod/` so production and dev have parallel structures.

### Change B1: config.py — Production databases go to data/prod/

Replace lines 20-24:

```python
# In dev mode, databases go to data/dev/ — production data is untouched.
# Vault, logs, and search limiter stay in data/ (shared).
if DEV_MODE:
    _DB_DIR = DATA_DIR / "dev"
else:
    _DB_DIR = DATA_DIR
```

With:

```python
# Databases go to data/prod/ or data/dev/.
# Vault, logs, search limiter, config, and backups stay in data/ (shared).
if DEV_MODE:
    _DB_DIR = DATA_DIR / "dev"
else:
    _DB_DIR = DATA_DIR / "prod"
```

### Change B2: server.py — Update dev mode copy paths

Replace lines 67-70:

```python
        dev_dir = Path(config.DATA_DIR) / "dev"
        prod_archive = Path(config.DATA_DIR) / "archive.db"
        prod_working = Path(config.DATA_DIR) / "working.db"
        prod_chroma = Path(config.DATA_DIR) / "chromadb"
```

With:

```python
        dev_dir = Path(config.DATA_DIR) / "dev"
        prod_archive = Path(config.DATA_DIR) / "prod" / "archive.db"
        prod_working = Path(config.DATA_DIR) / "prod" / "working.db"
        prod_chroma = Path(config.DATA_DIR) / "prod" / "chromadb"
```

### Change B3: Physical file move on Hades

**This must be done BEFORE starting the server with the new code.** Stop the server first.

```bash
cd ~/aion
mkdir -p data/prod
mv data/archive.db data/prod/
mv data/working.db data/prod/
mv data/chromadb data/prod/
```

Files that stay in `data/` (shared between prod and dev):
- `data/config.json`
- `data/secrets.enc`
- `data/.master_key`
- `data/search_usage.json`
- `data/logs/`
- `data/backups/`
- `data/dev/` (when it exists)

---

## What NOT to Do

- Do NOT move vault, logs, backups, search_usage, or config.json. Those stay in data/.
- Do NOT change backup.py — it uses config imports and will follow automatically.
- Do NOT change vault.py, search_limiter.py, or config_manager.py — they use DATA_DIR directly.
- Do NOT change the observer or pattern recognition logic — only the prompts and model references.
- Do NOT change chat.py, db.py, memory.py, or overnight.py.
- Do NOT change the OBSERVER_MODEL or PATTERN_MODEL variable assignments — they already reference CONSOLIDATION_MODEL which picks up the new default.

## Verification

### Model swap:
1. Run `python3 -c "from config import CONSOLIDATION_MODEL; print(CONSOLIDATION_MODEL)"` — should print `gpt-oss:20b`.
2. Run overnight on dev: `python overnight.py --dev`. Check logs — observer and consolidation should use gpt-oss:20b.

### Database move:
1. After the physical file move, start the server: `./start.sh → Production`. It should start normally with the databases found at `data/prod/`.
2. Send a message. Verify the response works and memories are retrieved.
3. Check that backup still works: `python backup.py`. Should find databases at their new locations.
4. Dev mode: `rm -rf data/dev && ./start.sh → Dev Mode`. Should copy from `data/prod/` to `data/dev/`.

### Prompt updates:
1. Run `python reobserve.py --dev` and check observation quality — should match the results from testing.
2. Run overnight on dev and check the self-knowledge narrative — should use second person and psychologist framing.
