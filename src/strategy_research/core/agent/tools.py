"""BaseTool + ToolRegistry: tool infrastructure.

Paradigm v2: every tool carries a machine-collected brief (注册时从
docstring 首行 + execute 签名 + effects 生成) and a full docstring
(详细版说明书, 经 tool_help 按需读取). See docs/agent-tools-reference.md.
"""

from __future__ import annotations

import copy
import inspect
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── 副作用声明 (paradigm v2) ────────────────────────────────────────
# effects 集合声明工具会写什么: db / fs / net。readonly 过滤在 P3 迁移
# 后由 effects 派生; 迁移前保持 is_readonly 类属性。
EFFECT_DB = "db"
EFFECT_FS = "fs"
EFFECT_NET = "net"

_EFFECT_LABELS = {
    EFFECT_DB: "写DB",
    EFFECT_FS: "写FS",
    EFFECT_NET: "网络",
}

# 注入参数: 不出现在 LLM schema / 必填列表 (ToolContext 化后在 P2 落地,
# 此处先排除以免误入必填)。
_INJECTED_PARAMS = {"self", "ctx", "workspace", "session_id", "_progress_callback"}


def _doc_first_line(tool_cls: type) -> str:
    """docstring 首行 = 简略版用途一句话 (与详细版同源)。"""
    doc = inspect.getdoc(tool_cls)
    if doc:
        line = doc.strip().splitlines()[0].strip()
        if line:
            return line[:80]
    return ""


def _required_params(tool: BaseTool) -> List[str]:
    """execute 显式签名中无默认值的参数; **kwargs 存量工具回退 parameters.required。"""
    try:
        sig = inspect.signature(tool.execute)
    except (TypeError, ValueError):
        return list(tool.parameters.get("required", []))
    if any(
        p.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        for p in sig.parameters.values()
    ):
        return list(tool.parameters.get("required", []))
    required = [
        name for name, p in sig.parameters.items()
        if name not in _INJECTED_PARAMS and p.default is p.empty
    ]
    return required or list(tool.parameters.get("required", []))


def _effects_label(tool: BaseTool) -> str:
    """effects 短标; 未声明时按 is_readonly 派生。"""
    if tool.effects:
        labels = [_EFFECT_LABELS[e] for e in sorted(tool.effects) if e in _EFFECT_LABELS]
        if labels:
            return ",".join(labels)
    return "只读" if getattr(tool, "is_readonly", True) else "写"


def _build_tool_brief(tool: BaseTool) -> str:
    """注册时生成简略版目录条目。"""
    summary = _doc_first_line(type(tool))
    required = _required_params(tool)
    parts = [f"- {tool.name}[{tool.category}]: {summary}"]
    if required:
        parts.append(f"必填: {', '.join(required)}")
    parts.append(f"副作用: {_effects_label(tool)}")
    return "；".join(parts)


def make_strict_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively inject OpenAI strict-mode requirements.

    OpenAI's structured outputs (`strict: True`) require:
    - ``additionalProperties: false`` at every object level
    - All properties in the ``required`` array

    This helper walks the schema tree and adds these constraints.
    For dict-shaped fields (``additionalProperties: {schema}``), we
    **drop** the schema (strict mode doesn't allow arbitrary keys) —
    callers should switch to explicit schemas if strict mode is needed.

    Args:
        schema: JSON Schema dict.

    Returns:
        A new schema (deep-copied) suitable for OpenAI strict mode.
    """
    schema = copy.deepcopy(schema)
    _walk_strict(schema)
    return schema


def _walk_strict(node: Any) -> None:
    """Mutate ``node`` in place to add strict-mode constraints."""
    if not isinstance(node, dict):
        return
    # If additionalProperties is a schema (dict-shape), strict mode can't
    # express arbitrary keys — drop it. Tool authors should use an explicit
    # schema or list shape instead.
    if isinstance(node.get("additionalProperties"), dict):
        del node["additionalProperties"]
    # Object types must have additionalProperties: false
    if node.get("type") == "object":
        node["additionalProperties"] = False
        # All listed properties must be required
        props = node.get("properties")
        if isinstance(props, dict) and props:
            existing = node.get("required") or []
            if not isinstance(existing, list):
                existing = list(existing)
            # Union of explicit required + all properties
            node["required"] = sorted(set(existing) | set(props.keys()))
    # Walk children — properties
    for prop_name, prop_val in (node.get("properties") or {}).items():
        if isinstance(prop_val, dict):
            _walk_strict(prop_val)
    # items (array element schema)
    items = node.get("items")
    if isinstance(items, dict):
        _walk_strict(items)
    elif isinstance(items, list):
        for v in items:
            if isinstance(v, dict):
                _walk_strict(v)
    # additionalProperties (if it's a dict schema — already handled above)
    # allOf / anyOf / oneOf
    for key in ("allOf", "anyOf", "oneOf"):
        for sub in node.get(key, []) or []:
            if isinstance(sub, dict):
                _walk_strict(sub)


class BaseTool(ABC):
    """Tool base class.

    Attributes:
        name: Unique tool identifier.
        description: Tool description shown to the LLM.
        parameters: Parameter definition in JSON Schema format.
        repeatable: Whether the tool may be called more than once.
        strict: If True, schema is converted to OpenAI strict-mode
            (structured outputs) before sending to the LLM.
    """

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}
    repeatable: bool = False
    is_readonly: bool = True
    strict: bool = False
    # ── paradigm v2 ─────────────────────────────────────────────
    category: str = "other"       # 领域分类: 文件/回测/因子/行情/分析/技能/Web/Goal/Shell
    effects: frozenset[str] = frozenset()  # {EFFECT_DB, EFFECT_FS, EFFECT_NET}
    brief: str = ""               # 注册时由 ToolRegistry 自动填充

    @classmethod
    def check_available(cls) -> bool:
        """Check if this tool's dependencies are met.

        Override in subclasses to check for API keys, packages, etc.
        Tools that return False are excluded from the registry.
        """
        return True

    @abstractmethod
    def execute(self, **kwargs: Any) -> str:
        """Execute the tool and return a JSON string."""

    def to_openai_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI function calling format.

        When ``strict=True``, the schema is rewritten to comply with
        OpenAI's structured outputs requirements and a ``strict`` flag
        is added so the provider enforces it server-side.
        """
        params = self.parameters or {"type": "object", "properties": {}, "required": []}
        if self.strict:
            params = make_strict_schema(params)
        fn_def: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "parameters": params,
        }
        if self.strict:
            fn_def["strict"] = True
        return {
            "type": "function",
            "function": fn_def,
        }


class ToolRegistry:
    """Tool registry."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool (collects its brief at registration time)."""
        self._tools[tool.name] = tool
        tool.brief = _build_tool_brief(tool)

    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieve a tool by name."""
        return self._tools.get(name)

    def all_tools(self) -> List[BaseTool]:
        """All registered tools (stable order)."""
        return list(self._tools.values())

    def get_definitions(self) -> List[Dict[str, Any]]:
        """Return all tools in OpenAI function calling format."""
        return [t.to_openai_schema() for t in self._tools.values()]

    def execute(self, name: str, params: Dict[str, Any]) -> str:
        """Execute a tool and guarantee a valid JSON return value."""
        tool = self._tools.get(name)
        if not tool:
            return json.dumps({"status": "error", "error": f"Tool '{name}' not found"}, ensure_ascii=False)
        try:
            return tool.execute(**params)
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return json.dumps({
                "status": "error", "tool": name,
                "error": str(exc),
            }, ensure_ascii=False)

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
