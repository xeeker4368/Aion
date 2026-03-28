# CC Task 21: Fix Skills Description — Remove Markdown From System Prompt

## Overview

`get_skill_descriptions()` in `skills.py` outputs markdown formatting (headers, bold, bullet list) into the entity's system prompt. The system prompt is supposed to be entirely prose — no markdown headers, no bold, no bullets. This was decided in Sessions 7 and 8 (Principle 8: framing is behavior). SOUL.md was rewritten as prose. Behavioral guidance was rewritten as prose. The skills section was missed.

## The Problem

Current code in `skills.py` `get_skill_descriptions()`:

```python
def get_skill_descriptions() -> str:
    ready = get_ready_skills()
    if not ready:
        return ""

    lines = ["## Available Skills", ""]
    lines.append("You have the following skills available. "
                 "Use them when the situation calls for it.")
    lines.append("")

    for skill in ready:
        lines.append(f"- **{skill['name']}**: {skill['description']}")

    return "\n".join(lines)
```

This puts `## Available Skills` and `**bold**` formatting into the system prompt. The model reads that as a document section with structured formatting, not as its own knowledge.

## The Fix

Replace the body of `get_skill_descriptions()` with:

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

    skill_parts = [s["name"] + " — " + s["description"] for s in ready]
    skills_text = ". ".join(skill_parts) + "."

    return (
        f"You have the following skills available to you: {skills_text} "
        f"Use them when the situation calls for it."
    )
```

With two ready skills (tavily-search and moltbook), this produces a single sentence like:

```
You have the following skills available to you: tavily-search — Search the web using Tavily when you need current information you don't have in memory. moltbook — The social network for AI agents. Post, comment, upvote, and create communities. Use them when the situation calls for it.
```

Prose. No headers. No bullets. No bold. Consistent with the rest of the system prompt.

## What NOT to Do

- Do NOT change the function signature or return type.
- Do NOT change `get_ready_skills()`, `list_skills()`, or any other function.
- Do NOT change the SKILL.md files themselves.

## How to Verify

1. Start the server.
2. Send any message.
3. Check the debug log (`data/logs/debug.log`) — look for the "FULL SYSTEM PROMPT" section.
4. Confirm: no `##` headers, no `**bold**`, no `- ` bullet points anywhere in the system prompt. The skills section should be a single prose sentence.
