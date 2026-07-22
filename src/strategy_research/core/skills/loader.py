"""SkillLoader — parse YAML frontmatter + Markdown body from SKILL.md files."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .models import Skill

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)


def parse_skill_file(path: Path) -> Skill | None:
    """Parse a SKILL.md file into a Skill object.

    Returns None if the file cannot be parsed or is missing required fields.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read %s: %s", path, exc)
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        # No frontmatter — treat entire file as content with name from filename
        return Skill(
            name=path.stem,
            content=text.strip(),
            path=path,
        )

    frontmatter_text = match.group(1)
    body = text[match.end():].strip()

    # Parse YAML frontmatter (minimal, no PyYAML dependency)
    meta = _parse_frontmatter(frontmatter_text)
    if not meta.get("name"):
        meta["name"] = path.stem

    return Skill(
        name=meta["name"],
        category=meta.get("category", "general"),
        description=meta.get("description", ""),
        tags=_parse_tags(meta.get("tags", [])),
        content=body,
        path=path,
    )


def _parse_frontmatter(text: str) -> dict[str, str | list[str]]:
    """Minimal YAML frontmatter parser (no PyYAML dependency)."""
    result: dict[str, str | list[str]] = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Handle YAML list syntax [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            result[key] = items
        else:
            result[key] = value.strip("'\"")
    return result


def _parse_tags(tags: str | list[str]) -> list[str]:
    """Normalize tags to list of strings."""
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if t]
    if isinstance(tags, str):
        # Handle "[a, b, c]" format
        if tags.startswith("[") and tags.endswith("]"):
            return [t.strip().strip("'\"") for t in tags[1:-1].split(",") if t.strip()]
        return [tags.strip()]
    return []
