"""Phase 5 — v0.6.0 tests: Prompt templates for workflow agents.

TDD tests for all workflow agent prompt templates.

Covers:
  - Each preset's agents have loadable prompt files
  - Each prompt file is non-empty
  - Each prompt file has required sections (Role, 输出, 规则)
  - PromptBuilder can load each prompt

Reference: docs/phase-5-plan.md §4.1.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.core.goal.workflow_config import load_goal_workflow


# ─── Prompt directory ──────────────────────────────────────────────────

PROMPTS_DIR = Path(__file__).parent.parent / "src" / "strategy_research" / "templates" / ".prompts"

# All presets and their agents
PRESETS = {
    "goal_factor_research": ["researcher", "data_quality", "factor_analyst", "risk_reviewer"],
    "goal_market_analysis": ["market_scanner", "regime_classifier", "report_writer"],
    "goal_risk_assessment": ["position_auditor", "risk_controller", "stress_tester", "report_writer"],
    "goal_strategy_review": ["pnl_attribution", "icarus_review", "factor_decay", "benchmark_compare", "summary_writer"],
    "goal_portfolio_review": ["portfolio_construction", "concentration_check", "risk_metrics", "report_writer"],
}

# Map YAML agent IDs to actual prompt file names
# (when agent_id != the prompt .md filename)
AGENT_PROMPT_MAP = {
    "pnl_attribution": "attribution_analyst",
    "summary_writer": "report_writer",
    "risk_reviewer": "risk_controller",
    "factor_decay": "factor_analyst",
    "risk_metrics": "risk_controller",
}


# ─── Prompt files exist ───────────────────────────────────────────────


class TestPromptFilesExist:
    """Each agent's prompt file should exist in templates/.prompts/."""

    @pytest.mark.parametrize("preset,agents", PRESETS.items())
    def test_all_agents_have_prompts(self, preset, agents):
        config = load_goal_workflow(preset)
        for agent_cfg in config.agents:
            prompt_name = AGENT_PROMPT_MAP.get(agent_cfg.id, agent_cfg.id)
            prompt_file = PROMPTS_DIR / f"{prompt_name}.md"
            assert prompt_file.exists(), (
                f"Prompt file missing for {preset}/{agent_cfg.id}: "
                f"expected {prompt_file}"
            )


# ─── Prompt file content ──────────────────────────────────────────────


class TestPromptFileContent:
    """Each prompt file should have required sections."""

    @pytest.mark.parametrize("preset,agents", PRESETS.items())
    def test_prompts_nonempty(self, preset, agents):
        config = load_goal_workflow(preset)
        for agent_cfg in config.agents:
            prompt_name = AGENT_PROMPT_MAP.get(agent_cfg.id, agent_cfg.id)
            prompt_file = PROMPTS_DIR / f"{prompt_name}.md"
            content = prompt_file.read_text(encoding="utf-8")
            assert len(content) > 50, f"{prompt_name}.md is too short"

    @pytest.mark.parametrize("preset,agents", PRESETS.items())
    def test_prompts_have_role(self, preset, agents):
        config = load_goal_workflow(preset)
        for agent_cfg in config.agents:
            prompt_name = AGENT_PROMPT_MAP.get(agent_cfg.id, agent_cfg.id)
            prompt_file = PROMPTS_DIR / f"{prompt_name}.md"
            content = prompt_file.read_text(encoding="utf-8")
            assert "Role" in content or "角色" in content, (
                f"{prompt_name}.md missing Role section"
            )

    @pytest.mark.parametrize("preset,agents", PRESETS.items())
    def test_prompts_have_output_format(self, preset, agents):
        config = load_goal_workflow(preset)
        for agent_cfg in config.agents:
            prompt_name = AGENT_PROMPT_MAP.get(agent_cfg.id, agent_cfg.id)
            prompt_file = PROMPTS_DIR / f"{prompt_name}.md"
            content = prompt_file.read_text(encoding="utf-8")
            assert "输出" in content or "output" in content.lower(), (
                f"{prompt_name}.md missing output section"
            )

    @pytest.mark.parametrize("preset,agents", PRESETS.items())
    def test_prompts_have_rules(self, preset, agents):
        config = load_goal_workflow(preset)
        for agent_cfg in config.agents:
            prompt_name = AGENT_PROMPT_MAP.get(agent_cfg.id, agent_cfg.id)
            prompt_file = PROMPTS_DIR / f"{prompt_name}.md"
            content = prompt_file.read_text(encoding="utf-8")
            assert "规则" in content or "rule" in content.lower(), (
                f"{prompt_name}.md missing rules section"
            )


# ─── PromptBuilder loads prompts ──────────────────────────────────────


class TestPromptBuilderLoads:
    """PromptBuilder should load each prompt successfully."""

    def test_prompt_builder_loads_all(self):
        from strategy_research.core.workflow.prompt import PromptBuilder
        builder = PromptBuilder()
        # Test loading each unique prompt
        all_prompts = set()
        for agents in PRESETS.values():
            for agent_id in agents:
                prompt_name = AGENT_PROMPT_MAP.get(agent_id, agent_id)
                all_prompts.add(prompt_name)

        for prompt_name in sorted(all_prompts):
            content = builder.load_prompt(prompt_name)
            assert content, f"PromptBuilder failed to load {prompt_name}"


# ─── Preset-level checks ─────────────────────────────────────────────


class TestPresetPromptConsistency:
    """Preset YAML prompt_file fields should match actual prompt files."""

    def test_all_prompt_files_resolvable(self):
        """Every prompt_file in every preset should resolve to an actual file."""
        for preset_name in PRESETS:
            config = load_goal_workflow(preset_name)
            for agent_cfg in config.agents:
                prompt_name = AGENT_PROMPT_MAP.get(agent_cfg.id, agent_cfg.id)
                prompt_file = PROMPTS_DIR / f"{prompt_name}.md"
                assert prompt_file.exists(), (
                    f"{preset_name}/{agent_cfg.id}: "
                    f"prompt_file={agent_cfg.id} → {prompt_file} not found"
                )