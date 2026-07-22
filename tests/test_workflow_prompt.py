import pytest
from pathlib import Path
from strategy_research.core.workflow.prompt import PromptBuilder


class TestPromptBuilder:
    def test_load_prompt_exists(self, tmp_path):
        prompt_file = tmp_path / "test_agent.md"
        prompt_file.write_text("# Test Agent\nYou are a test agent.")
        builder = PromptBuilder(tmp_path)
        content = builder.load_prompt("test_agent")
        assert content == "# Test Agent\nYou are a test agent."

    def test_load_prompt_missing(self, tmp_path):
        builder = PromptBuilder(tmp_path)
        content = builder.load_prompt("nonexistent")
        assert content == ""

    def test_load_prompt_caches(self, tmp_path):
        prompt_file = tmp_path / "agent.md"
        prompt_file.write_text("v1")
        builder = PromptBuilder(tmp_path)
        assert builder.load_prompt("agent") == "v1"
        prompt_file.write_text("v2")
        assert builder.load_prompt("agent") == "v1"

    def test_clear_cache(self, tmp_path):
        prompt_file = tmp_path / "agent.md"
        prompt_file.write_text("v1")
        builder = PromptBuilder(tmp_path)
        assert builder.load_prompt("agent") == "v1"
        prompt_file.write_text("v2")
        builder.clear_cache()
        assert builder.load_prompt("agent") == "v2"

    def test_build_prompt_template_only(self, tmp_path):
        prompt_file = tmp_path / "researcher.md"
        prompt_file.write_text("# Researcher\nDo research.")
        builder = PromptBuilder(tmp_path)
        result = builder.build_prompt("researcher")
        assert result == "# Researcher\nDo research."

    def test_build_prompt_base_only(self, tmp_path):
        builder = PromptBuilder(tmp_path)
        result = builder.build_prompt("unknown", base_prompt="Custom prompt")
        assert result == "Custom prompt"

    def test_build_prompt_template_and_base(self, tmp_path):
        prompt_file = tmp_path / "agent.md"
        prompt_file.write_text("# Agent\nBe helpful.")
        builder = PromptBuilder(tmp_path)
        result = builder.build_prompt("agent", base_prompt="Extra instructions")
        assert "# Agent\nBe helpful." in result
        assert "Extra instructions" in result

    def test_build_prompt_with_upstream(self, tmp_path):
        builder = PromptBuilder(tmp_path)
        upstream = {"data_quality": {"completeness": 0.95, "status": "ok"}}
        result = builder.build_prompt("agent", upstream_outputs=upstream)
        assert "Upstream Agent Outputs" in result
        assert "data_quality" in result

    def test_build_prompt_with_context(self, tmp_path):
        builder = PromptBuilder(tmp_path)
        result = builder.build_prompt("agent", context={"round": 1, "calmar": 0.5})
        assert "Current Context" in result
        assert "round" in result

    def test_build_prompt_no_empty_sections(self, tmp_path):
        builder = PromptBuilder(tmp_path)
        result = builder.build_prompt("agent")
        assert result == ""

    def test_format_upstream_dict(self, tmp_path):
        builder = PromptBuilder(tmp_path)
        upstream = {"agent_a": {"key1": "val1", "key2": "val2"}}
        result = builder._format_upstream(upstream)
        assert "agent_a" in result
        assert "key1" in result
        assert "val1" in result

    def test_format_context_skips_input_from(self, tmp_path):
        builder = PromptBuilder(tmp_path)
        context = {"round": 1, "input_from": {"a": {}}}
        result = builder._format_context(context)
        assert "round" in result
        assert "input_from" not in result
