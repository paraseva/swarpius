from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from app.coordinator.skill_docs import AgentSkillDocument, AgentSkillMetadata

_log = logging.getLogger("swarpius.skill_loader")

_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_XML_TAG_PATTERN = re.compile(r"<[^>]+>")


def _extract_frontmatter_and_body(skill_md_path: Path) -> tuple[dict[str, Any], str]:
    content = skill_md_path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        raise ValueError(f"{skill_md_path} is missing YAML frontmatter")

    closing_idx = content.find("\n---\n", 4)
    if closing_idx == -1:
        raise ValueError(f"{skill_md_path} has unterminated YAML frontmatter")

    raw_frontmatter = content[4:closing_idx]
    body = content[closing_idx + len("\n---\n") :].strip()
    try:
        parsed = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as exc:
        raise ValueError(f"{skill_md_path} has invalid YAML frontmatter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{skill_md_path} frontmatter must be a YAML mapping")
    return parsed, body


def _validate_skill_name(skill_name: str, parent_dir_name: str, skill_md_path: Path) -> None:
    if not (1 <= len(skill_name) <= 64):
        raise ValueError(f"{skill_md_path} name must be 1-64 characters")
    if skill_name != parent_dir_name:
        raise ValueError(
            f"{skill_md_path} name '{skill_name}' must match directory '{parent_dir_name}'",
        )
    if "--" in skill_name:
        raise ValueError(f"{skill_md_path} name must not contain consecutive hyphens")
    if not _SKILL_NAME_PATTERN.match(skill_name):
        raise ValueError(
            f"{skill_md_path} name must be lowercase alphanumeric with hyphens only",
        )
    if _XML_TAG_PATTERN.search(skill_name):
        raise ValueError(f"{skill_md_path} name must not contain XML tags")


def _validate_skill_description(description: str, skill_md_path: Path) -> None:
    if not (1 <= len(description) <= 1024):
        raise ValueError(f"{skill_md_path} description must be 1-1024 characters")
    if _XML_TAG_PATTERN.search(description):
        raise ValueError(f"{skill_md_path} description must not contain XML tags")


_CRITICAL_START = re.compile(r"^\s*<!--\s*critical\s*-->\s*$", re.MULTILINE)
_CRITICAL_END = re.compile(r"^\s*<!--\s*/critical\s*-->\s*$", re.MULTILINE)


def extract_critical_directives(body: str) -> tuple[str, str]:
    """Extract <!-- critical --> blocks from a skill doc body.

    Returns (critical_text, cleaned_body).  critical_text is the
    concatenated content of all critical blocks (empty string if none).
    cleaned_body has the critical blocks removed and excessive blank
    lines collapsed.
    """
    if not body or "<!-- critical" not in body:
        return "", body

    critical_parts: list[str] = []
    cleaned_lines: list[str] = []
    in_critical = False

    for line in body.split("\n"):
        if _CRITICAL_START.match(line):
            in_critical = True
            continue
        if _CRITICAL_END.match(line):
            in_critical = False
            continue
        if in_critical:
            critical_parts.append(line)
        else:
            cleaned_lines.append(line)

    critical_text = "\n".join(critical_parts).strip()
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return critical_text, cleaned


def load_agent_skills(skills_dir: Path) -> list[AgentSkillDocument]:
    skills: list[AgentSkillDocument] = []
    skill_paths = sorted(skills_dir.glob("*/SKILL.md"))
    if not skill_paths:
        raise ValueError(f"No Agent SKILL.md files found in {skills_dir}")
    for path in skill_paths:
        frontmatter, body = _extract_frontmatter_and_body(path)
        name = str(frontmatter.get("name") or "").strip()
        description = str(frontmatter.get("description") or "").strip()
        _validate_skill_name(name, path.parent.name, path)
        _validate_skill_description(description, path)
        compatibility = frontmatter.get("compatibility")
        if compatibility is not None and not (1 <= len(str(compatibility)) <= 500):
            raise ValueError(f"{path} compatibility must be 1-500 characters when provided")

        metadata_map = frontmatter.get("metadata")
        if metadata_map is not None and not isinstance(metadata_map, dict):
            raise ValueError(f"{path} metadata must be a key-value mapping")

        requires_env_raw = frontmatter.get("requires_env")
        requires_env = str(requires_env_raw).strip() if requires_env_raw else None
        if requires_env and not os.environ.get(requires_env, "").strip():
            _log.info(
                "Skipping skill '%s' — %s is not set",
                name, requires_env,
            )
            continue

        requires_tool_raw = frontmatter.get("requires_tool")
        requires_tool = str(requires_tool_raw).strip() if requires_tool_raw else None

        metadata = AgentSkillMetadata(
            name=name,
            description=description,
            location=str(path.resolve()),
            license=frontmatter.get("license"),
            compatibility=str(compatibility) if compatibility is not None else None,
            metadata={str(k): str(v) for k, v in metadata_map.items()} if metadata_map else None,
            allowed_tools=frontmatter.get("allowed-tools"),
            requires_env=requires_env,
            requires_tool=requires_tool,
        )
        skills.append(AgentSkillDocument(metadata=metadata, body=body))

    return skills


def filter_skills_by_registered_tools(
    skills: list[AgentSkillDocument],
    registered_tool_names: set[str],
) -> list[AgentSkillDocument]:
    """Drop skills whose ``requires_tool`` is not in the registry.

    Run after tool registration. Env-var presence is not enough to
    decide a tool-gated skill: the factory may decline to build the
    tool even when the credential is set (provider mismatch, unknown
    value, explicit `none`). The registry is the source of truth.
    """
    kept: list[AgentSkillDocument] = []
    for skill in skills:
        tool_name = skill.metadata.requires_tool
        if tool_name and tool_name not in registered_tool_names:
            _log.info(
                "Skipping skill '%s' — required tool '%s' is not registered",
                skill.metadata.name, tool_name,
            )
            continue
        kept.append(skill)
    return kept


def format_agent_skills_for_prompt(skills: list[AgentSkillDocument]) -> tuple[str, str]:
    """Format skills into a prompt block and extract critical directives.

    Returns (skills_block, key_rules).  Critical directives (marked with
    ``<!-- critical -->`` in skill docs) are extracted from individual
    skills and returned separately so they can be placed in the
    highest-attention position (end of context, just before user message).
    key_rules is empty string if no critical markers are found.
    """
    key_rules_parts: list[str] = []
    sections: list[str] = []

    for skill in skills:
        runtime_name = skill.metadata.name.replace("-", "_")
        raw_body = (skill.body or "").strip()
        critical_text, cleaned_body = extract_critical_directives(raw_body)

        if critical_text:
            key_rules_parts.append(f"### {runtime_name}\n{critical_text}")

        parts = [
            "<skill>",
            f"  <name>{runtime_name}</name>",
            f"  <description>{skill.metadata.description}</description>",
        ]
        if cleaned_body:
            parts.append(f"  <instructions>\n{cleaned_body}\n  </instructions>")
        parts.append("</skill>")
        sections.append("\n".join(parts))

    skills_block = "<available_skills>\n" + "\n\n".join(sections) + "\n</available_skills>"
    key_rules = "\n\n".join(key_rules_parts) if key_rules_parts else ""
    return skills_block, key_rules
