"""PromptBuilder — Phase 4/5 of the Chat Agent refactor.

Unifies four parallel ``system_prompt`` loading paths:
- ``api/routers/chat.py:_get_system_prompt`` (web chat, removed in Phase 5)
- ``api/session/service.py`` (chat service)
- ``cli/tui/session.py`` (TUI chat/goal mode)
- ``core/agent/role_factory._load_role_system_prompt`` (9 Goal roles)

Design (Phase 5):
- Strategy pattern: ``ChatPromptBuilder`` / ``StaticFilePromptBuilder``
- Templates live in ``src/strategy_research/templates/.prompts/*.md`` (the
  actual asset location; Phase 4's ``core/agent/templates/*.md.j2`` was
  removed because it duplicated existing ``.prompts/*.md`` files).
- ``ChatPromptBuilder`` renders ``chat.md`` with ``str.format()`` to
  preserve the existing Python-style placeholders (``{workspace}`` /
  ``{tool_list}``); undeclared placeholders return the raw text (literal
  behavior — matches what callers observed before Phase 4).
- ``StaticFilePromptBuilder(role)`` returns ``<role>.md`` verbatim, no
  rendering. Matches ``role_factory._load_role_system_prompt`` behavior
  where ``{strategy_name}`` / ``{workspace}`` are literal text.
- ``PromptBuilderFactory.get(unknown_role)`` returns ``_NullBuilder``
  instead of raising — preserves the legacy "unknown role → empty
  string → stub fallback" behavior that ``role_factory`` callers depend on.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ── Templates directory ─────────────────────────────────────────────────


_PROMPTS_DIR = (
    Path(__file__).parent.parent.parent / "templates" / ".prompts"
)
# Resolves to: src/strategy_research/templates/.prompts/


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


# ── Default token limit ────────────────────────────────────────────────


DEFAULT_TOKEN_LIMIT = 128_000  # matches GPT-4-class context windows


def _estimate_chars(messages: list[Message]) -> int:
    """Cheap char-count proxy: 4 chars ≈ 1 token."""
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)


# ── Implementations ────────────────────────────────────────────────────


class ChatPromptBuilder:
    """Loads ``chat.md`` and renders with plain ``str.replace()``.

    Variables in ``chat.md`` (4 total):
        ``{workspace}``    — workspace root path
        ``{tool_list}``    — comma-separated tool names
        ``{name}``         — strategy name placeholder in path examples
        ``{策略名}``       — Chinese equivalent of ``{name}``

    Why ``str.replace()`` instead of ``str.format()``:
        ``chat.md`` contains Python dict literals (``{"top_n": 10, ...}``)
        that ``str.format()`` interprets as ``{key:format_spec}`` syntax and
        raises ``KeyError``. ``str.replace()`` is a literal substring
        replacement — no parsing — so it coexists with Python code samples.

    Fallback: ``FALLBACK_PROMPT`` constant when ``chat.md`` is missing.
    """

    FALLBACK_PROMPT = (
        "你是 QuantNodes-Research 的量化金融助手。"
        "用自然语言回复，简洁直接。"
    )

    # (placeholder_in_text, context_key) pairs. Order matters when one
    # placeholder is a prefix of another (none currently overlap).
    _PLACEHOLDERS: tuple[tuple[str, str], ...] = (
        ("{workspace}", "workspace"),
        ("{tool_list}", "tool_list"),
        ("{name}", "name"),
        ("{策略名}", "策略名"),
    )

    def __init__(self, token_limit: int = DEFAULT_TOKEN_LIMIT) -> None:
        self._token_limit = token_limit
        self._path = _PROMPTS_DIR / "chat.md"

    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        if not self._path.exists():
            return self.FALLBACK_PROMPT
        text = self._path.read_text(encoding="utf-8")
        for placeholder, key in self._PLACEHOLDERS:
            text = text.replace(placeholder, context.get(key, ""))
        return text

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


class StaticFilePromptBuilder:
    """Loads ``<role>.md`` as-is (no rendering).

    Used for the 9 roles in ``role_factory._ROLE_PROMPT_FILES``. Placeholders
    like ``{strategy_name}`` / ``{workspace}`` are returned as literal text —
    matches existing ``role_factory._load_role_system_prompt`` behavior where
    ``_prompts_dir() / <role>.md`` is read with ``read_text()`` and never
    formatted.
    """

    def __init__(self, role: str, token_limit: int = DEFAULT_TOKEN_LIMIT) -> None:
        self._role = role
        self._token_limit = token_limit
        self._path = _PROMPTS_DIR / f"{role}.md"

    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding="utf-8")

    def build_messages(
        self,
        user_query: str,
        history: list[Message],
        context: dict[str, Any],
    ) -> list[Message]:
        system = self.build_system_prompt(self._role, context)
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


class _NullBuilder:
    """Returned by ``PromptBuilderFactory.get(unknown_role)``.

    Preserves the legacy ``role_factory._load_role_system_prompt`` behavior:
    unknown role → empty string → stub fallback in ``build_agent_loop``.
    """

    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        return ""

    def build_messages(
        self,
        user_query: str,
        history: list[Message],
        context: dict[str, Any],
    ) -> list[Message]:
        return []

    def estimate_tokens(self, messages: list[Message]) -> int:
        return 0

    def validate(self, messages: list[Message]) -> ValidationResult:
        return ValidationResult(ok=True)


# ── Factory (strategy dispatch) ───────────────────────────────────────


class PromptBuilderFactory:
    """Switch by role / mode. Mirrors ``AgentRunnerFactory`` pattern.

    Unknown roles return ``_NullBuilder()`` (empty prompt) instead of
    raising — preserves backward compatibility with callers that pass
    arbitrary role names (e.g. ``role_factory.build_agent_loop``).
    """

    _BUILDERS: dict[str, PromptBuilder] = {
        "chat": ChatPromptBuilder(),
        "researcher": StaticFilePromptBuilder("researcher"),
        "data_quality": StaticFilePromptBuilder("data_quality"),
        "factor_analyst": StaticFilePromptBuilder("factor_analyst"),
        "strategist": StaticFilePromptBuilder("strategist"),
        "portfolio_construction": StaticFilePromptBuilder(
            "portfolio_construction"
        ),
        "risk_controller": StaticFilePromptBuilder("risk_controller"),
        "attribution_analyst": StaticFilePromptBuilder("attribution_analyst"),
        "anti_overfit_analyst": StaticFilePromptBuilder("anti_overfit_analyst"),
        "backtest_diagnostics": StaticFilePromptBuilder("backtest_diagnostics"),
        "critic": StaticFilePromptBuilder("critic"),
    }

    @classmethod
    def get(cls, role: str) -> PromptBuilder:
        if role not in cls._BUILDERS:
            return _NullBuilder()
        return cls._BUILDERS[role]

    @classmethod
    def list_roles(cls) -> list[str]:
        return list(cls._BUILDERS.keys())

    @classmethod
    def register(cls, role: str, builder: PromptBuilder) -> None:
        """Register a new builder at runtime (mirrors ``AgentRunnerRegistry``)."""
        cls._BUILDERS[role] = builder


__all__ = [
    "ChatPromptBuilder",
    "Message",
    "PromptBuilder",
    "PromptBuilderFactory",
    "StaticFilePromptBuilder",
    "ValidationResult",
]
