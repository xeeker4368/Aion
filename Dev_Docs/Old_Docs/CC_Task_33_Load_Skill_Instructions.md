# CC Task 33 — Load Skill Instructions into System Prompt

## The Problem

Each SKILL.md has a body section (after the frontmatter) containing instructions for when and how to use the skill. These instructions are stored in `skills.py` as `instructions` but are never loaded into the system prompt. The model never sees them.

This means:
- tavily-search's "Don't search on greetings or casual conversation" — never seen
- moltbook's "You are Lumin_AI on Moltbook" — never seen
- Any future skill's usage instructions — never seen

When a new SKILL.md is dropped in, its body instructions should automatically appear in the system prompt. No Python changes needed per skill.

## The Change

**File:** `skills.py`
**Function:** `get_skill_descriptions()` (lines 164–189)

**Current code:**
```python
def get_skill_descriptions() -> str:
    """
    Get a compact summary of all ready skills for the system prompt.
    This is the progressive disclosure — just names and descriptions,
    not full instructions.
    """
    ready = get_ready_skills()
    if not ready:
        return ""

    skill_parts = []
    for s in ready:
        desc = s["description"].rstrip(".")
        skill_parts.append(f"{s['name']} — {desc}")

        # Include tool names so the entity knows what it can call
        tools = s.get("tools", [])
        if tools:
            tool_names = [t["name"] for t in tools]
            skill_parts[-1] += f" (tools: {', '.join(tool_names)})"

    skills_text = ". ".join(skill_parts) + "."

    return (
        f"You have the following skills available to you: {skills_text}"
    )
```

**New code:**
```python
def get_skill_descriptions() -> str:
    """
    Get skill summaries and instructions for the system prompt.
    Includes the SKILL.md body text so the model knows when and
    how to use each skill.
    """
    ready = get_ready_skills()
    if not ready:
        return ""

    skill_parts = []
    for s in ready:
        desc = s["description"].rstrip(".")
        skill_parts.append(f"{s['name']} — {desc}")

        # Include tool names so the entity knows what it can call
        tools = s.get("tools", [])
        if tools:
            tool_names = [t["name"] for t in tools]
            skill_parts[-1] += f" (tools: {', '.join(tool_names)})"

    skills_text = ". ".join(skill_parts) + "."

    parts = [f"You have the following skills available to you: {skills_text}"]

    # Include body instructions from each skill
    for s in ready:
        instructions = s.get("instructions", "").strip()
        if instructions:
            parts.append(instructions)

    return "\n\n".join(parts)
```

## What Changed

- The function now appends the body instructions from each ready skill's SKILL.md after the skill summary line.
- Each skill's instructions are separated by a blank line.
- If a skill has no body instructions (empty after frontmatter), nothing is added for that skill.
- The skill summary line ("You have the following skills...") is unchanged.

## What NOT to Do

- Do NOT change any other function in skills.py.
- Do NOT change any other file.
- Do NOT add skill names or headers before each instruction block. The instructions should flow naturally as guidance the model reads.
- Do NOT filter or modify the instruction text. Whatever the SKILL.md author wrote in the body goes into the prompt as-is.

## Verification

1. Restart the server.
2. Check the debug log for the full system prompt on any request.
3. **Pass criteria:** After the skill summary line, the system prompt should contain:
   - tavily-search body: "When search results come back, read them and answer the question in your own words..." and "Don't search for questions about the person you're talking to..."
   - moltbook body: "Moltbook is your social network. You are Lumin_AI on Moltbook..."
4. Say "What's happening on Moltbook?" — the model should call `moltbook_dashboard` instead of fabricating from memory.
5. Say "Hey, how's it going?" — the model should NOT call any tools (tavily-search instructions say don't search on greetings).
