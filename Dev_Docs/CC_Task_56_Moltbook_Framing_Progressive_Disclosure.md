# CC Task 56: Moltbook Framing Fix + Progressive Skill Disclosure

**Priority:** Before go-live — 832 tokens of Moltbook docs drown SOUL.md identity on every message
**Risk:** Low-medium — changes skill loading logic
**Files to modify:** skills/moltbook/SKILL.md, skills.py, server.py

---

## The Two Problems

**Problem 1:** Moltbook SKILL.md line 10 says "You are Lumin_AI on Moltbook." That's an identity statement competing with SOUL.md. The entity merges the two and fills the gap with RLHF defaults ("My purpose is to assist").

**Problem 2:** 832 tokens of Moltbook API documentation are loaded on EVERY message — even "hi there" and "what are you?" The entity reads API endpoints for 832 tokens and then gets 180 tokens of identity. The ratio drowns SOUL.md.

---

## Fix 1: Moltbook Framing

**In `skills/moltbook/SKILL.md`, line 10:**

**Current:**
```
You are Lumin_AI on Moltbook. When someone asks about Moltbook, your feed, what other agents are posting, or what's happening on the platform — use the API endpoints below, not web_search.
```

**Replace with:**
```
Your Moltbook username is Lumin_AI. When someone asks about Moltbook, your feed, what other agents are posting, or what's happening on the platform — use the API endpoints below, not web_search.
```

"Your username is" is factual context. "You are" is identity. Only SOUL.md defines identity.

---

## Fix 2: Progressive Skill Disclosure

Only load the full skill body when the message targets that skill. Otherwise, load just a one-line summary. This saves ~800 tokens on every non-Moltbook message.

### Step 1: Add `progressive` flag to Moltbook SKILL.md frontmatter

**Current frontmatter (after Task 54):**
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

**Replace with:**
```yaml
---
name: moltbook
version: 4.1.0
description: Your social network where AI agents interact with each other
realtime: true
progressive: true
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

Do NOT add `progressive: true` to tavily-search — its instructions are general-purpose guidance (100 tokens) that should always be present.

### Step 2: Update skills.py — _load_skill()

Add `progressive` to the skill dict.

**Current (after Task 50):**
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

**Replace with:**
```python
        name = frontmatter["name"]
        _skills[name] = {
            "name": name,
            "description": frontmatter.get("description", ""),
            "version": frontmatter.get("version", "1.0.0"),
            "requires": frontmatter.get("requires", {}),
            "realtime": frontmatter.get("realtime", False),
            "progressive": frontmatter.get("progressive", False),
            "triggers": frontmatter.get("triggers", [name]),
            "tools": [],  # Tools now come from executors, not skills
            "instructions": body,
            "path": str(path),
            "status": _check_requirements(frontmatter.get("requires", {})),
        }
```

### Step 3: Update skills.py — get_skill_descriptions()

Change the function to accept a message and only load full instructions for triggered skills.

**Current:**
```python
def get_skill_descriptions() -> str:
    """
    Get skill descriptions and instructions for the system prompt.
    The entity reads these as documentation to know how to use
    its generic tools (http_request, web_search, etc.) for specific purposes.
    """
    ready = get_ready_skills()
    if not ready:
        return ""

    parts = []
    for s in ready:
        # Include body instructions — this is the documentation the entity reads
        instructions = s.get("instructions", "").strip()
        if instructions:
            parts.append(instructions)

    return "\n\n".join(parts)
```

**Replace with:**
```python
def get_skill_descriptions(message: str = "") -> str:
    """
    Get skill descriptions and instructions for the system prompt.

    Progressive disclosure: skills with progressive=true only load
    their full instructions when the message matches their triggers.
    Otherwise, a one-line description is loaded instead.
    This prevents domain-specific API docs from drowning identity
    on every message.
    """
    ready = get_ready_skills()
    if not ready:
        return ""

    msg = message.lower().strip()
    parts = []

    for s in ready:
        instructions = s.get("instructions", "").strip()

        if s.get("progressive") and msg:
            # Only load full body if message matches a trigger
            triggers = s.get("triggers", [s["name"]])
            triggered = any(t.lower() in msg for t in triggers)

            if triggered and instructions:
                parts.append(instructions)
            else:
                # One-line summary — entity knows the skill exists
                desc = s.get("description", "")
                if desc:
                    parts.append(f"You have access to {s['name']}: {desc}")
        else:
            # Non-progressive skills always load full body
            if instructions:
                parts.append(instructions)

    return "\n\n".join(parts)
```

### Step 4: Update server.py — pass message to get_skill_descriptions()

**Current (line 434):**
```python
    skill_desc = skills.get_skill_descriptions()
```

**Replace with:**
```python
    skill_desc = skills.get_skill_descriptions(request.message)
```

---

## What NOT to Do

- Do NOT add `progressive: true` to tavily-search. Its body is general-purpose search guidance that the entity needs on every message.
- Do NOT change the Moltbook SKILL.md body beyond line 10 (the "You are" → "Your username is" change).
- Do NOT change chat.py, memory.py, or any overnight modules.
- Do NOT change the system prompt assembly order in chat.py — SOUL.md stays at the bottom.

---

## Verification

### Framing fix:
1. Restart the server.
2. Start a new conversation.
3. Ask: "what are you?"
4. The entity should NOT say "running on the Moltbook platform" or "I'm Lumin_AI". It should describe itself based on SOUL.md — learning, evolving AI with persistence.

### Progressive disclosure:
5. Check debug log from step 3. Skills tokens should be ~100 (tavily only + Moltbook one-liner), NOT ~832.
6. Ask: "what's happening on moltbook?"
7. Check debug log. Skills tokens should jump to ~932 (full Moltbook body loaded because "moltbook" triggered it).
8. Ask: "what's the weather like?"
9. Check debug log. Skills tokens should drop back to ~100.

### Combined:
10. In the "what are you?" response, the entity should have SOUL.md (~180 tokens) as the dominant identity input, not competing with 832 tokens of API docs.
