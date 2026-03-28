"""
Aion Skill Loader

Reads SKILL.md files from the skills directory. Each skill is a
markdown file with YAML frontmatter that declares metadata,
required executors, and required config/secrets.

Progressive disclosure (borrowed from OpenClaw):
- At startup, only names and descriptions are loaded
- Full instructions are loaded into context when relevant

Adding a new skill = drop a SKILL.md into the skills directory.
"""

import logging
import re
from pathlib import Path

import yaml

import executors
import vault
from config import BASE_DIR

logger = logging.getLogger("aion.skills")

SKILLS_DIR = BASE_DIR / "skills"

# Loaded skills: name -> skill data
_skills: dict[str, dict] = {}


def init_skills():
    """Scan skills directory and load all SKILL.md files."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    _skills.clear()

    for skill_dir in SKILLS_DIR.iterdir():
        if skill_dir.is_dir():
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                _load_skill(skill_file)

    # Also check for standalone SKILL.md files (not in subdirectories)
    for skill_file in SKILLS_DIR.glob("*.md"):
        if skill_file.name != "README.md":
            _load_skill(skill_file)

    logger.info(f"Loaded {len(_skills)} skills: {list(_skills.keys())}")


def _load_skill(path: Path):
    """Load a single SKILL.md file."""
    try:
        raw = path.read_text()
        frontmatter, body = _parse_frontmatter(raw)

        if not frontmatter or "name" not in frontmatter:
            logger.warning(f"Skipping {path}: no name in frontmatter")
            return

        name = frontmatter["name"]
        _skills[name] = {
            "name": name,
            "description": frontmatter.get("description", ""),
            "version": frontmatter.get("version", "1.0.0"),
            "requires": frontmatter.get("requires", {}),
            "realtime": frontmatter.get("realtime", False),
            "tools": frontmatter.get("tools", []),
            "instructions": body,
            "path": str(path),
            "status": _check_requirements(frontmatter.get("requires", {})),
        }

        logger.info(f"Loaded skill: {name} ({_skills[name]['status']['summary']})"
                     f"{' [realtime]' if _skills[name]['realtime'] else ''}")

    except Exception as e:
        logger.error(f"Failed to load skill from {path}: {e}")


def _parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """
    Parse YAML frontmatter from a markdown file.
    Frontmatter is between --- markers at the start of the file.
    """
    pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)'
    match = re.match(pattern, text, re.DOTALL)

    if not match:
        return None, text

    try:
        frontmatter = yaml.safe_load(match.group(1))
        body = match.group(2).strip()
        return frontmatter, body
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse YAML frontmatter: {e}")
        return None, text


def _check_requirements(requires: dict) -> dict:
    """
    Check if a skill's requirements are met.
    Returns a status dict with details.
    """
    result = {
        "ready": True,
        "summary": "ready",
        "missing_executors": [],
        "missing_config": [],
    }

    # Check executors
    required_executors = requires.get("executors", [])
    available = executors.list_executors()
    for exe in required_executors:
        if exe not in available:
            result["missing_executors"].append(exe)
            result["ready"] = False

    # Check config/secrets
    required_config = requires.get("config", [])
    for key in required_config:
        if not vault.has(key):
            result["missing_config"].append(key)
            result["ready"] = False

    if not result["ready"]:
        parts = []
        if result["missing_executors"]:
            parts.append(f"missing executors: {result['missing_executors']}")
        if result["missing_config"]:
            parts.append(f"missing config: {result['missing_config']}")
        result["summary"] = "not ready — " + ", ".join(parts)

    return result


def get_skill(name: str) -> dict | None:
    """Get a skill by name."""
    return _skills.get(name)


def list_skills() -> list[dict]:
    """List all skills with their status (without full instructions)."""
    return [
        {
            "name": s["name"],
            "description": s["description"],
            "version": s["version"],
            "status": s["status"],
            "tool_count": len(s.get("tools", [])),
        }
        for s in _skills.values()
    ]


def get_ready_skills() -> list[dict]:
    """Get only skills that have all requirements met."""
    return [s for s in _skills.values() if s["status"]["ready"]]


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


def get_tool_definitions() -> list[dict]:
    """
    Generate Ollama-compatible tool definitions from all ready skills.
    These get passed to client.chat(tools=...) so the model can call them.

    Returns a list of tool dicts in the Ollama format:
    [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]
    """
    ready = get_ready_skills()
    definitions = []

    for skill in ready:
        for tool in skill.get("tools", []):
            # Build the parameters schema
            properties = {}
            required = []

            for param_name, param_info in tool.get("parameters", {}).items():
                properties[param_name] = {
                    "type": param_info.get("type", "string"),
                    "description": param_info.get("description", ""),
                }
                if param_info.get("required", False):
                    required.append(param_name)

            # Build the tool description
            tool_description = tool["description"]

            # If the skill is realtime, tell the model this is live data
            if skill.get("realtime", False):
                tool_description += (
                    " This returns live data that changes frequently."
                    " Always use this tool to check current information"
                    " rather than relying on what you remember from previous checks."
                )

            definition = {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool_description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }

            definitions.append(definition)

    return definitions


def get_tool_map() -> dict:
    """
    Build a lookup from tool name to execution info.
    Used by the server to execute tool calls from the model.

    Returns: {tool_name: {"executor": str, "executor_args": dict, "url_template": str|None, "skill_name": str}}
    """
    ready = get_ready_skills()
    tool_map = {}

    for skill in ready:
        for tool in skill.get("tools", []):
            tool_map[tool["name"]] = {
                "executor": tool.get("executor", ""),
                "executor_args": tool.get("executor_args", {}),
                "url_template": tool.get("url_template"),
                "skill_name": skill["name"],
            }

    return tool_map


def get_skill_instructions(name: str) -> str | None:
    """
    Get the full instructions for a skill.
    Called when the entity decides to use a skill and needs the details.
    """
    skill = _skills.get(name)
    if skill and skill["status"]["ready"]:
        return skill["instructions"]
    return None


def refresh_skill_status():
    """Re-check requirements for all skills. Call after config changes."""
    for name, skill in _skills.items():
        skill["status"] = _check_requirements(skill.get("requires", {}))


def install_skill(filename: str, content: str) -> dict:
    """
    Install a skill from uploaded SKILL.md content.
    Returns the skill data with status.
    """
    # Parse to get the name
    frontmatter, body = _parse_frontmatter(content)
    if not frontmatter or "name" not in frontmatter:
        return {"error": "Invalid SKILL.md: no name in frontmatter"}

    name = frontmatter["name"]

    # Create skill directory
    skill_dir = SKILLS_DIR / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Write the file
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(content)

    # Load it
    _load_skill(skill_path)

    skill = _skills.get(name)
    if skill:
        return {
            "name": skill["name"],
            "description": skill["description"],
            "status": skill["status"],
        }

    return {"error": "Failed to load skill after install"}


def uninstall_skill(name: str) -> bool:
    """Remove a skill."""
    skill = _skills.get(name)
    if not skill:
        return False

    # Remove from registry
    del _skills[name]

    # Remove file
    path = Path(skill["path"])
    if path.exists():
        path.unlink()
        # Remove directory if empty
        if path.parent != SKILLS_DIR and not any(path.parent.iterdir()):
            path.parent.rmdir()

    return True
