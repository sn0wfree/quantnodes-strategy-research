"""Extract JSON output schemas from agent prompt markdown files.

Reads ``.prompts/<role>.md``, locates the output JSON block (under
``## 输出`` / ``## 输出格式`` / ``## 输出 Schema``), and returns a
structured ``AgentSchema`` describing each field's label, type, role as
core/non-core, and optional enum/format hints.

The parser handles two JSON presentation styles found in the prompt
files:

  1. **Fenced** — ``\\`\\`\\`json ... \\`\\`\\```
  2. **Raw** — bare ``{ ... }`` blocks (no language tag)

Field annotations are inline ``# @hint`` comments placed on the JSON
line *above* the field definition.  Recognized hints:

  ``@label: <text>``    — human-readable field name (Chinese preferred)
  ``@core: true|false`` — whether to show by default (vs. collapsible)
  ``@type: <type>``     — explicit type override
  ``@enum: {JSON}``     — enum value mapping
  ``@format: <fmt>``    — display format hint
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Section detection ──────────────────────────────────────────────────

# Pattern: heading line starting with ## that mentions output/schema
_OUTPUT_SECTION_RE = re.compile(
    r"^##\s+(输出|输出格式|输出 Schema|输出格式（严格 JSON）)",
    re.MULTILINE,
)

# Fenced JSON block: ```json ... ``` or ``` ... ```
_FENCED_JSON_RE = re.compile(
    r"```(?:json)?\s*\n(\{[\s\S]*?\})\n```",
    re.MULTILINE,
)

# @hint pattern: # @label: X @type: Y @core: true
# Value is non-greedy and stops at the next " @key:" or end-of-line so a
# single line can carry multiple hints.
_HINT_RE = re.compile(r"@(\w+):\s*(.*?)(?=\s+@\w+:|$)")

# ── Data classes ───────────────────────────────────────────────────────

@dataclass
class FieldHints:
    """Metadata hints attached to a JSON field via ``# @hint`` comments."""
    label: str | None = None
    type: str | None = None            # bool|number|string|enum|array|object|weights_table
    enum_values: Dict[str, str] | None = None   # {raw_value: display_label}
    format: str | None = None          # percentage|currency|comma_separated
    core: bool | None = None           # True = show by default
    description: str | None = None     # tooltip text


@dataclass
class AgentSchema:
    """Structured representation of an agent's JSON output schema."""
    role: str
    fields: List[str]                   # ordered list of field names
    field_hints: Dict[str, FieldHints]  # field_name → hints
    action_field: str | None = None     # 'action' if action-type output
    action_enum: List[str] | None = None


# ── Core field heuristic ───────────────────────────────────────────────

# Fields that are decisional / conclusion-like and should be shown by default.
_DEFAULT_CORE_FIELDS = {
    "action", "verdict", "risk_passed", "risk_rating", "overfit_passed",
    "hypothesis", "reason", "recommendation", "status", "level",
    "method", "weighted_score", "analysis",
}

# Fields that are data/detail-like and should be collapsible.
_DEFAULT_NON_CORE_FIELDS = {
    "weights", "risk_contributions", "var_95", "cvar_95", "max_drawdown",
    "stress_results", "tail_risk", "thresholds", "config_audit",
    "evidence_contribution", "human_guidance_observed", "decision_blockers",
    "open_required_items", "thresholds_breached", "predicted_affected",
    "avoid_actions", "bias_check", "context_notes",
    "methods_passed", "metrics", "suggestions",
}


def _is_core_field(name: str, hints: FieldHints | None) -> bool:
    """Determine if a field should be shown by default."""
    if hints and hints.core is not None:
        return hints.core
    if name in _DEFAULT_CORE_FIELDS:
        return True
    if name in _DEFAULT_NON_CORE_FIELDS:
        return False
    # Unknown field: use type heuristic
    if hints and hints.type:
        if hints.type in ("bool", "enum"):
            return True  # decisional
    return False  # default: collapsible


# ── JSON block extraction ──────────────────────────────────────────────

def _find_output_section(text: str) -> tuple[int, int]:
    """Find the line range of the output/schema section.

    Returns (start_line, end_line) where start_line is the heading line
    and end_line is the next ``## `` heading or EOF.
    """
    m = _OUTPUT_SECTION_RE.search(text)
    if not m:
        return -1, -1
    start = m.start()
    # Find next ## heading after the match
    next_heading = re.search(r"^## ", text[m.end():], re.MULTILINE)
    if next_heading:
        end = m.end() + next_heading.start()
    else:
        end = len(text)
    return start, end


def _extract_json_blocks(section_text: str) -> List[str]:
    """Extract JSON blocks from a section — both fenced and raw.

    Priority: fenced first (more explicit), then raw blocks.
    """
    blocks = []
    # 1. Fenced blocks (```json ... ``` or ``` ... ```)
    for m in _FENCED_JSON_RE.finditer(section_text):
        blocks.append(m.group(1))
    # 2. Raw blocks (bare { ... } not inside fences)
    if not blocks:
        # Only try raw if no fenced found
        idx = 0
        while idx < len(section_text):
            start = section_text.find("{", idx)
            if start == -1:
                break
            # Check this isn't inside a fenced block
            fence_before = section_text.rfind("```", 0, start)
            if fence_before > 0:
                fence_close = section_text.find("```", fence_before + 3)
                if fence_close > start:
                    idx = start + 1
                    continue
            # Find balanced end
            end = _find_balanced_end(section_text, start)
            if end > start:
                candidate = section_text[start:end + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        blocks.append(candidate)
                except json.JSONDecodeError:
                    pass
                idx = end + 1
            else:
                idx = start + 1
    return blocks


def _find_balanced_end(text: str, start_idx: int) -> int:
    """Find matching closing brace via depth counting, respecting strings."""
    depth = 0
    i = start_idx
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


# ── Hint parsing ───────────────────────────────────────────────────────

def _parse_hints_from_line(line: str) -> Dict[str, str]:
    """Parse ``# @key: value`` pairs from a single line.

    Returns a dict like ``{"label": "是否通过", "type": "bool"}``.
    """
    hints = {}
    for m in _HINT_RE.finditer(line):
        key, value = m.group(1), m.group(2).strip()
        hints[key] = value
    return hints


def _build_field_hints(raw_hints: Dict[str, str]) -> FieldHints:
    """Convert raw hint strings into a typed FieldHints."""
    h = FieldHints()

    if "label" in raw_hints:
        h.label = raw_hints["label"]

    if "type" in raw_hints:
        h.type = raw_hints["type"]

    if "format" in raw_hints:
        h.format = raw_hints["format"]

    if "description" in raw_hints:
        h.description = raw_hints["description"]

    if "core" in raw_hints:
        h.core = raw_hints["core"].lower() in ("true", "1", "yes")

    if "enum" in raw_hints:
        try:
            h.enum_values = json.loads(raw_hints["enum"])
        except (json.JSONDecodeError, TypeError):
            h.enum_values = None

    return h


def _infer_type(value: Any) -> str:
    """Infer field type from a sample value."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) or isinstance(value, float):
        return "number"
    if isinstance(value, list):
        if not value:
            return "array"
        if all(isinstance(x, str) for x in value):
            return "array"
        if all(isinstance(x, dict) for x in value):
            return "array"
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str):
        # Check for common enum patterns
        if "|" in value and len(value) < 100:
            return "enum"
        return "string"
    return "string"


# ── Main API ───────────────────────────────────────────────────────────

def extract_agent_schema(role: str, prompt_dir: Path | None = None) -> AgentSchema | None:
    """Extract JSON output schema from an agent's prompt file.

    Args:
        role: Agent role identifier (e.g. ``"researcher"``)
        prompt_dir: Path to ``templates/.prompts/`` directory.
            If None, uses the default relative to this file.

    Returns:
        AgentSchema with field metadata, or None if the prompt has no
        parseable JSON output section.
    """
    if prompt_dir is None:
        prompt_dir = (
            Path(__file__).parent.parent.parent / "templates" / ".prompts"
        )

    prompt_path = prompt_dir / f"{role}.md"
    if not prompt_path.exists():
        logger.debug("No prompt file for role=%s at %s", role, prompt_path)
        return None

    try:
        text = prompt_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read prompt for role=%s: %s", role, exc)
        return None

    # Find the output/schema section. A prompt may declare SEVERAL output
    # sections (e.g. "## 输出格式（严格 JSON）" with rules only, then a
    # later "## 输出" carrying the actual JSON example) — iterate all
    # matches and use the first section that yields a JSON block.
    blocks: List[str] = []
    section = ""
    for m in _OUTPUT_SECTION_RE.finditer(text):
        next_heading = re.search(r"^## ", text[m.end():], re.MULTILINE)
        end = m.end() + next_heading.start() if next_heading else len(text)
        candidate = text[m.start():end]
        found = _extract_json_blocks(candidate)
        if found:
            blocks = found
            section = candidate
            break

    if not blocks:
        logger.debug("No JSON blocks found in any output section for role=%s", role)
        return None

    # Parse the first (main) JSON block
    main_block = blocks[0]
    try:
        main_obj = json.loads(main_block)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON block for role=%s", role)
        return None

    if not isinstance(main_obj, dict):
        return None

    # Collect hints from the section lines above each JSON key.
    # We walk the section and look for # @hint lines followed by a JSON field.
    hints_by_key = _collect_hints(section, main_obj)

    # Build field metadata
    fields: List[str] = []
    field_hints: Dict[str, FieldHints] = {}

    for key in main_obj.keys():
        fields.append(key)
        raw = hints_by_key.get(key, {})
        hints = _build_field_hints(raw)

        # Infer type from sample value if not explicitly set
        if not hints.type:
            hints.type = _infer_type(main_obj[key])

        # Auto-derive enum_values from "A | B | C" sample strings when no
        # explicit @enum annotation exists (values map to themselves; the
        # frontend or a later @enum hint supplies display labels).
        if not hints.enum_values and hints.type == "enum":
            val = main_obj[key]
            if isinstance(val, str) and "|" in val:
                parts = [v.strip() for v in val.split("|") if v.strip()]
                if parts:
                    hints.enum_values = {p: p for p in parts}

        # Infer core status
        hints.core = _is_core_field(key, hints)

        field_hints[key] = hints

    # Detect action-type output
    action_field = None
    action_enum = None
    if "action" in main_obj:
        action_field = "action"
        val = main_obj["action"]
        if isinstance(val, str) and "|" in val:
            action_enum = [v.strip() for v in val.split("|")]

    return AgentSchema(
        role=role,
        fields=fields,
        field_hints=field_hints,
        action_field=action_field,
        action_enum=action_enum,
    )


def _collect_hints(section: str, main_obj: dict) -> Dict[str, Dict[str, str]]:
    """Walk the output section and collect # @hint comments keyed by field name.

    Strategy: scan lines before each top-level JSON field key and collect
    any ``# @key: value`` annotations found.
    """
    hints: Dict[str, Dict[str, str]] = {}
    pending_hints: Dict[str, str] = {}

    for line in section.splitlines():
        stripped = line.strip()
        # Collect hint lines
        parsed = _parse_hints_from_line(stripped)
        if parsed:
            pending_hints.update(parsed)
            continue

        # Detect JSON field lines: ``"key": value,`` or ``"key": {``
        field_match = re.match(r'\s*"(\w+)"\s*:', stripped)
        if field_match and pending_hints:
            key = field_match.group(1)
            if key in main_obj:
                hints[key] = dict(pending_hints)
            pending_hints = {}

    return hints


# ── Module-level helpers for external callers ──────────────────────────

def load_all_schemas(prompt_dir: Path | None = None) -> Dict[str, AgentSchema]:
    """Load schemas for all agents with prompt files in the directory.

    Returns ``{role: schema}`` for all successfully parsed roles.
    """
    if prompt_dir is None:
        prompt_dir = (
            Path(__file__).parent.parent.parent / "templates" / ".prompts"
        )

    results: Dict[str, AgentSchema] = {}
    if not prompt_dir.exists():
        return results

    for md_path in sorted(prompt_dir.glob("*.md")):
        role = md_path.stem
        # Skip private/common files
        if role.startswith("_"):
            continue
        schema = extract_agent_schema(role, prompt_dir)
        if schema:
            results[role] = schema

    return results
