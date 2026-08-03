"""Phase 6 P1 — chat-mode AgentLoop 构造工厂.

Unifies the 3 inline ``AgentLoop(...)`` constructors used by chat paths:

- ``api/routers/chat.py`` (web chat)
- ``cli/tui/session.py`` (TUI chat / goal mode)
- ``api/session/service.py`` (SessionService background runner)

Centralizes chat-mode defaults (``stream_mode=True``, ``max_iterations=1``,
``compact_config`` from ``cfg``, default ``registry``, goal/hypothesis
auto-create disabled) and exposes a single parameter surface so P2
(``allowed_tools`` unlock) and P3 (``{workspace}`` / ``{tool_list}``
rendering) can be applied uniformly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .loop import AgentLoop


def build_chat_agent_loop(
    config: Any,
    session_id: str,
    *,
    role: str = "chat",
    workspace: Path | None = None,
    allowed_tools: list[str] | None = None,
    max_iterations: int = 1,
    on_event: Callable | None = None,
    event_bus: Any | None = None,
    strategy_name: str | None = None,
    extra_context: dict[str, Any] | None = None,
    system_prompt_override: str | None = None,
    registry: Any = None,
    compact_config: Any | None = None,
) -> AgentLoop:
    """Construct a chat-mode ``AgentLoop``.

    Differences from ``role_factory.build_agent_loop``:

    - ``stream_mode`` forced to True (token-by-token streaming)
    - ``max_iterations`` defaults to 1 (single-pass chat, no ReAct loop)
    - ``compact_config`` taken from ``config.compact_config`` unless
      explicitly overridden
    - ``registry`` defaults to ``build_default_registry()`` (safe fallback
      to ``None`` if import fails)
    - ``enable_goal_injection`` and ``enable_hypothesis_auto_create``
      are disabled (chat mode is non-agentic)
    - ``system_prompt`` is rendered via ``PromptBuilderFactory.get(role)``
      with ``extra_context`` (e.g. ``{"workspace": ..., "tool_list": ...}``)
      unless ``system_prompt_override`` is provided

    P2 (``allowed_tools`` unlock):
        ``allowed_tools`` defaults to ``None`` (= all tools). Previously
        web chat passed ``[]`` (= no tools) — that hard lock is removed.

    P3 (workspace / tool_list rendering):
        When ``workspace`` is provided, it is included in the
        ``PromptBuilderFactory`` context so the ``{workspace}`` /
        ``{tool_list}`` placeholders in ``chat.md`` render with real values.
        When ``workspace`` is ``None``, ``{workspace}`` stays empty and
        ``{tool_list}`` still renders from the default registry (so the LLM
        sees what tools are available even without a workspace).
    """
    from .builtin_tools import build_default_registry
    from .prompt_builder import PromptBuilderFactory

    # Tool registry (default: all built-in tools)
    if registry is None:
        try:
            registry = build_default_registry()
        except Exception:
            registry = None

    # System prompt via PromptBuilderFactory (P3: pass real workspace/tool_list)
    if system_prompt_override is not None:
        system_prompt: str | None = system_prompt_override
    else:
        ctx = dict(extra_context or {})
        if workspace is not None:
            ctx.setdefault("workspace", str(workspace))
        else:
            ctx.setdefault("workspace", "")
        if "tool_list" not in ctx and registry is not None:
            try:
                tool_names = sorted(registry._tools.keys())
                ctx["tool_list"] = "\n".join(f"- {n}" for n in tool_names)
            except Exception:
                ctx["tool_list"] = ""
        system_prompt = PromptBuilderFactory.get(role).build_system_prompt(
            role, ctx
        )

    # Compact config: prefer explicit override, then config.compact_config
    if compact_config is None and config is not None:
        compact_config = getattr(config, "compact_config", None)

    return AgentLoop(
        config=config,
        registry=registry,
        workspace=workspace,
        on_event=on_event,
        stream_mode=True,  # chat mode: token-by-token streaming
        max_iterations=max_iterations,
        session_id=session_id,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,  # P2: default None (all tools)
        enable_goal_injection=False,  # chat mode: no goal injection
        enable_hypothesis_auto_create=False,
        strategy_name=strategy_name,
        compact_config=compact_config,
        event_bus=event_bus,
    )


__all__ = ["build_chat_agent_loop"]
