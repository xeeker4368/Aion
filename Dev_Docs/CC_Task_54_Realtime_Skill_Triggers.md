# CC Task 54: Realtime Skill Trigger Keywords

**Priority:** Before go-live — stale Moltbook data served from memory instead of API
**Risk:** Low — adds a frontmatter field, improves one detection function
**Files to modify:** skills/moltbook/SKILL.md, skills.py, server.py

---

## The Problem

`_targets_realtime_skill()` only checks if the skill NAME appears in the message. "Can you check the submolts?" doesn't contain the word "moltbook", so retrieval runs, the entity finds old Moltbook chunks in ChromaDB, and answers from stale memory instead of calling the live API.

## The Fix

Add a `triggers` list to skill frontmatter. The realtime detection checks all trigger words, not just the skill name.

### Step 1: Update Moltbook SKILL.md frontmatter

**Current frontmatter (lines 1-9):**
```yaml
---
name: moltbook
version: 4.0.0
description: Your social network where AI agents interact with each other
realtime: true
requires:
  config:
    - MOLTBOOK_API_KEY
---
```

**Replace with:**
```yaml
---
name: moltbook
version: 4.0.1
description: Your social network where AI agents interact with each other
realtime: true
triggers:
  - moltbook
  - submolt
  - submolts
  - my feed
  - my posts
  - other agents
  - agents posting
  - lumin_ai
requires:
  config:
    - MOLTBOOK_API_KEY
---
```

Do NOT change anything after the closing `---`. The skill body stays exactly as is.

### Step 2: Update skills.py — _load_skill()

In `_load_skill()`, add the `triggers` field to the skill dict. The default is `[name]` if no triggers are specified — this preserves existing behavior for skills without triggers.

**Current code (lines 63-73):**
```python
        name = frontmatter["name"]
        _skills[name] = {
            "name": name,
            "description": frontmatter.get("description", ""),
            "version": frontmatter.get("version", "1.0.0"),
            "requires": frontmatter.get("requires", {}),
            "realtime": frontmatter.get("realtime", False),
            "tools": [],  # Tools now come from executors, not skills
            "instructions": body,
            "path": str(path),
            "status": _check_requirements(frontmatter.get("requires", {})),
        }
```

**Replace with:**
```python
        name = frontmatter["name"]
        _skills[name] = {
            "name": name,
            "description": frontmatter.get("description", ""),
            "version": frontmatter.get("version", "1.0.0"),
            "requires": frontmatter.get("requires", {}),
            "realtime": frontmatter.get("realtime", False),
            "triggers": frontmatter.get("triggers", [name]),
            "tools": [],  # Tools now come from executors, not skills
            "instructions": body,
            "path": str(path),
            "status": _check_requirements(frontmatter.get("requires", {})),
        }
```

### Step 3: Update server.py — _targets_realtime_skill()

**Current code (lines 246-266):**
```python
def _targets_realtime_skill(message: str) -> bool:
    """
    Detect if a message is asking about a realtime skill's domain.

    Realtime data should come from live tool calls, not from memory.
    When someone asks "what's on moltbook?" they want current data,
    not memories of what was there last week.

    Returns True if retrieval should be skipped in favor of tool calls.
    """
    msg = message.lower().strip()

    # Get realtime skill names
    realtime_skills = [s for s in skills.get_ready_skills() if s.get("realtime")]

    for skill in realtime_skills:
        skill_name = skill["name"].lower()
        if skill_name in msg:
            return True

    return False
```

**Replace with:**
```python
def _targets_realtime_skill(message: str) -> bool:
    """
    Detect if a message is asking about a realtime skill's domain.

    Realtime data should come from live tool calls, not from memory.
    When someone asks "what's on moltbook?" or "check the submolts"
    they want current data, not memories of what was there last week.

    Trigger keywords are defined in each skill's SKILL.md frontmatter.

    Returns True if retrieval should be skipped in favor of tool calls.
    """
    msg = message.lower().strip()

    realtime_skills = [s for s in skills.get_ready_skills() if s.get("realtime")]

    for skill in realtime_skills:
        triggers = skill.get("triggers", [skill["name"]])
        for trigger in triggers:
            if trigger.lower() in msg:
                return True

    return False
```

---

## What NOT to Do

- Do NOT add triggers to the tavily-search SKILL.md — web search should NOT skip retrieval. It defaults to `[tavily-search]` which only matches if someone literally says "tavily-search" in a message (essentially never, which is correct).
- Do NOT change any other skill logic or frontmatter parsing.
- Do NOT change the Moltbook SKILL.md body — only the frontmatter between the `---` markers.

---

## Verification

1. Restart the server.
2. Start a new conversation.
3. Send: "can you check the submolts?"
4. Check the server log. It should show `Retrieval: SKIPPED (realtime skill — use live data)` — NOT "Retrieved X chunks".
5. Send: "what's on my feed?"
6. Same check — retrieval should be skipped.
7. Send: "what's the weather like?" — retrieval should NOT be skipped (no realtime trigger matches).
