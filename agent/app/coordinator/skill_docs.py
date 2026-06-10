from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel


class AgentSkillMetadata(BaseModel):
    """Metadata loaded from SKILL.md frontmatter."""

    name: str
    description: str
    location: str
    license: Optional[str] = None
    compatibility: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None
    allowed_tools: Optional[str] = None
    # Optional env-var name. When set, the skill loader skips this
    # skill unless the named env var is present and non-empty. Lets
    # feature-gated skills declare their hard dependency in-place
    # rather than being filtered by the caller.
    requires_env: Optional[str] = None
    # Optional tool name. When set, the runtime drops this skill from
    # the prompt unless the named tool ends up registered. Used for
    # skills whose effective availability depends on multiple env
    # vars resolving into a working tool (web-search needs *one* of
    # SearXNG/Brave/Tavily *and* the factory must successfully build
    # the tool — env presence alone is not enough).
    requires_tool: Optional[str] = None


class AgentSkillDocument(BaseModel):
    """Full agent skill document (metadata + markdown body)."""

    metadata: AgentSkillMetadata
    body: str
