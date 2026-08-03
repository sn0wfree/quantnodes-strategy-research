"""PromptBuilder — Phase 4 of the Chat Agent refactor.

Unifies the three parallel system_prompt loading paths:
- chat.py / cli/tui / session.py  (chat mode)
- role_factory._load_role_system_prompt (Goal mode, 9 roles)

Design:
- Strategy pattern: ChatPromptBuilder / ResearcherPromptBuilder / ...
- Templates in core/agent/templates/*.md.j2 (Jinja2)
- Implements the Phase 2 PromptBuilder Protocol:
    build_system_prompt(role, context) -> str
    build_messages(user_query, history, context) -> list[Message]
    estimate_tokens(messages) -> int
    validate(messages) -> ValidationResult
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ── Templates directory ─────────────────────────────────────────────────


_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _template_path(name: str) -> Path:
    """Resolve a template name to a file under core/agent/templates/."""
    if not name.endswith(".md.j2"):
        name = f"{name}.md.j2"
    return _TEMPLATES_DIR / name


# ── Validation ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a built message list (Phase 3 robustness)."""

    ok: bool
    error: str = ""


# ── Message type alias ─────────────────────────────────────────────────


Message = dict[str, Any]


# ── Protocol (Phase 2 contract) ────────────────────────────────────────


class PromptBuilder(Protocol):
    """Build system + user prompts for a given role. See phase2-interfaces.md."""

    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str: ...

    def build_messages(
        self,
        user_query: str,
        history: list[Message],
        context: dict[str, Any],
    ) -> list[Message]: ...

    def estimate_tokens(self, messages: list[Message]) -> int: ...

    def validate(self, messages: list[Message]) -> ValidationResult: ...


# ── Jinja2 helpers ─────────────────────────────────────────────────────


def _get_jinja_env():  # type: ignore[no-untyped-def]
    """Lazy jinja2 import + env (kept in try/except so tests can run without it)."""
    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined

        return Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),  # resolves at call time
            autoescape=False,
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
    except ImportError as exc:
        raise RuntimeError(
            "jinja2 is required for PromptBuilder but not installed"
        ) from exc


# ── Default token limit ────────────────────────────────────────────────


DEFAULT_TOKEN_LIMIT = 128_000  # matches GPT-4-class context windows


def _estimate_chars(messages: list[Message]) -> int:
    """Cheap char-count proxy: 4 chars ≈ 1 token."""
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)


# ── Implementations ────────────────────────────────────────────────────


class ChatPromptBuilder:
    """For conversational chat mode (natural language output)."""

    TEMPLATE_NAME = "chat"

    def __init__(self, token_limit: int = DEFAULT_TOKEN_LIMIT) -> None:
        self._token_limit = token_limit
        self._template = _get_jinja_env().get_template(f"{self.TEMPLATE_NAME}.md.j2")

    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        return self._template.render(
            role=role,
            workspace=context.get("workspace", ""),
            tool_list=context.get("tool_list", ""),
            mode=context.get("mode", "chat"),
        )

    def build_messages(
        self,
        user_query: str,
        history: list[Message],
        context: dict[str, Any],
    ) -> list[Message]:
        system = self.build_system_prompt("chat", context)
        messages: list[Message] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_query})
        return messages

    def estimate_tokens(self, messages: list[Message]) -> int:
        return _estimate_chars(messages) // 4

    def validate(self, messages: list[Message]) -> ValidationResult:
        tokens = self.estimate_tokens(messages)
        if tokens > self._token_limit:
            return ValidationResult(
                ok=False,
                error=(
                    f"Messages exceed token limit: {tokens}/{self._token_limit}. "
                    "Consider /clear or /compact."
                ),
            )
        return ValidationResult(ok=True)


class ResearcherPromptBuilder:
    """For the researcher role (Goal mode, structured JSON output)."""

    TEMPLATE_NAME = "researcher"

    def __init__(self, token_limit: int = DEFAULT_TOKEN_LIMIT) -> None:
        self._token_limit = token_limit
        self._template = _get_jinja_env().get_template(f"{self.TEMPLATE_NAME}.md.j2")

    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        return self._template.render(
            goal_id=context.get("goal_id", ""),
            criteria=context.get("criteria", []),
            workspace_path=context.get("workspace_path", ""),
        )

    def build_messages(
        self,
        user_query: str,
        history: list[Message],
        context: dict[str, Any],
    ) -> list[Message]:
        system = self.build_system_prompt("researcher", context)
        messages: list[Message] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_query})
        return messages

    def estimate_tokens(self, messages: list[Message]) -> int:
        return _estimate_chars(messages) // 4

    def validate(self, messages: list[Message]) -> ValidationResult:
        tokens = self.estimate_tokens(messages)
        if tokens > self._token_limit:
            return ValidationResult(
                ok=False,
                error=f"Messages exceed token limit: {tokens}/{self._token_limit}.",
            )
        return ValidationResult(ok=True)


# ── Factory (strategy dispatch) ───────────────────────────────────────


class PromptBuilderFactory:
    """Switch by role / mode. Mirrors AgentRunnerFactory pattern."""

    _BUILDERS: dict[str, type[PromptBuilder]] = {
        "chat": ChatPromptBuilder,
        "researcher": ResearcherPromptBuilder,
    }

    @classmethod
    def get(cls, role: str) -> PromptBuilder:
        if role not in cls._BUILDERS:
            raise ValueError(
                f"Unknown role: {role!r}. "
                f"Valid: {list(cls._BUILDERS.keys())}"
            )
        return cls._BUILDERS[role]()

    @classmethod
    def list_roles(cls) -> list[str]:
        return list(cls._BUILDERS.keys())

    @classmethod
    def register(cls, role: str, builder_cls: type[PromptBuilder]) -> None:
        """Register a new builder at runtime (mirrors AgentRunnerRegistry)."""
        cls._BUILDERS[role] = builder_cls


__all__ = [
    "ChatPromptBuilder",
    "Message",
    "PromptBuilder",
    "PromptBuilderFactory",
    "ResearcherPromptBuilder",
    "ValidationResult",
]
