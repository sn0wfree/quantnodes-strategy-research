"""Tests for Phase 4 PromptBuilder — 10 boundary cases.

See docs/chat-agent-refactor-phase4-prompt-builder.md §6 for the
full test specification.
"""
from __future__ import annotations

import pytest

# ── ChatPromptBuilder ─────────────────────────────────────────────────


class TestChatPromptBuilder:
    def test_empty_history_returns_system_and_user(self):
        from strategy_research.core.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        messages = builder.build_messages(
            user_query="hi",
            history=[],
            context={"workspace": "/tmp/ws", "tool_list": "[]", "mode": "chat"},
        )
        # system + user = 2 messages (no history appended)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "hi"}
        assert "/tmp/ws" in messages[0]["content"]

    def test_long_history_preserved(self):
        from strategy_research.core.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        history = [
            {"role": "user", "content": f"msg-{i}"}
            for i in range(1000)
        ]
        messages = builder.build_messages(
            user_query="final",
            history=history,
            context={"workspace": "/tmp"},
        )
        # system + 1000 history + user = 1002
        assert len(messages) == 1002
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "final"

    def test_special_chars_render_safely(self):
        from strategy_research.core.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        messages = builder.build_messages(
            user_query='quote "test" newline\ntab\tbackslash\\end',
            history=[],
            context={"workspace": "/w", "tool_list": "[]"},
        )
        # special chars must round-trip through jinja2
        content = messages[-1]["content"]
        assert '"test"' in content
        assert "\n" in content
        assert "\t" in content
        assert "\\end" in content  # backslash preserved

    def test_unicode_and_emoji(self):
        from strategy_research.core.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        messages = builder.build_messages(
            user_query="中文 🐂 测试",
            history=[],
            context={"workspace": "/tmp"},
        )
        assert "中文" in messages[-1]["content"]
        assert "🐂" in messages[-1]["content"]

    def test_template_missing_raises(self, tmp_path, monkeypatch):
        # Force jinja2 to look in an empty dir
        from strategy_research.core.agent import prompt_builder as pb_mod

        monkeypatch.setattr(pb_mod, "_TEMPLATES_DIR", tmp_path)

        from strategy_research.core.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        # Template lookup happens at __init__ via _get_jinja_env().get_template,
        # so the TemplateNotFound is raised during construction.
        with pytest.raises(Exception, match="chat.md.j2"):
            ChatPromptBuilder()

    def test_validate_ok(self):
        from strategy_research.core.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder(token_limit=128_000)
        messages = builder.build_messages(
            user_query="hi", history=[], context={"workspace": "/w"}
        )
        result = builder.validate(messages)
        assert result.ok is True
        assert result.error == ""

    def test_validate_overflow(self):
        from strategy_research.core.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder(token_limit=10)  # tiny limit
        messages = builder.build_messages(
            user_query="hi", history=[], context={"workspace": "/w"}
        )
        result = builder.validate(messages)
        assert result.ok is False
        assert "exceed" in result.error.lower()


# ── ResearcherPromptBuilder ───────────────────────────────────────────


class TestResearcherPromptBuilder:
    def test_criteria_rendered_in_template(self):
        from strategy_research.core.agent.prompt_builder import (
            ResearcherPromptBuilder,
        )

        builder = ResearcherPromptBuilder()
        system = builder.build_system_prompt(
            role="researcher",
            context={
                "goal_id": "g-1",
                "criteria": ["a", "b", "c"],
                "workspace_path": "/w",
            },
        )
        # All three criteria must appear as bullet items
        assert "- a" in system
        assert "- b" in system
        assert "- c" in system
        assert "g-1" in system
        assert "/w" in system


# ── PromptBuilderFactory ──────────────────────────────────────────────


class TestPromptBuilderFactory:
    def test_unknown_role_raises_value_error(self):
        from strategy_research.core.agent.prompt_builder import (
            PromptBuilderFactory,
        )

        with pytest.raises(ValueError, match="Unknown role"):
            PromptBuilderFactory.get("unknown")

    def test_chat_role_returns_chat_builder(self):
        from strategy_research.core.agent.prompt_builder import (
            ChatPromptBuilder,
            PromptBuilderFactory,
        )

        builder = PromptBuilderFactory.get("chat")
        assert isinstance(builder, ChatPromptBuilder)

    def test_researcher_role_returns_researcher_builder(self):
        from strategy_research.core.agent.prompt_builder import (
            PromptBuilderFactory,
            ResearcherPromptBuilder,
        )

        builder = PromptBuilderFactory.get("researcher")
        assert isinstance(builder, ResearcherPromptBuilder)

    def test_list_roles_includes_default(self):
        from strategy_research.core.agent.prompt_builder import (
            PromptBuilderFactory,
        )

        roles = PromptBuilderFactory.list_roles()
        assert "chat" in roles
        assert "researcher" in roles
