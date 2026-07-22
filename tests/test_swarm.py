"""Tests for Swarm system (runtime, preset loader, CLI)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.core.swarm.preset_loader import load_preset, list_presets
from strategy_research.core.swarm.runtime import (
    AgentResult,
    SwarmPreset,
    SwarmResult,
    SwarmRuntime,
)
from strategy_research.core.workflow.types import AgentCall, AgentStatus


# ── Preset Loader Tests ────────────────────────────────────


class TestPresetLoader:
    def test_load_preset(self, tmp_path):
        preset_file = tmp_path / "test.yaml"
        preset_file.write_text(
            "name: test_preset\n"
            "description: A test preset\n"
            "agents:\n"
            "  - id: agent1\n"
            "    prompt_file: .prompts/a.md\n"
            "    tools: [read_file]\n"
            "  - id: agent2\n"
            "    prompt_file: .prompts/b.md\n"
            "    input_from: [agent1]\n"
            "    tools: [write_file]\n"
            "dag:\n"
            "  agent1: []\n"
            "  agent2: [agent1]\n"
        )
        preset = load_preset(preset_file)
        assert preset is not None
        assert preset.name == "test_preset"
        assert preset.description == "A test preset"
        assert len(preset.agents) == 2
        assert preset.dag["agent1"] == []
        assert preset.dag["agent2"] == ["agent1"]

    def test_load_preset_nonexistent(self, tmp_path):
        preset = load_preset(tmp_path / "nonexistent.yaml")
        assert preset is None

    def test_load_preset_invalid_yaml(self, tmp_path):
        preset_file = tmp_path / "bad.yaml"
        preset_file.write_text("{{invalid yaml")
        preset = load_preset(preset_file)
        assert preset is None

    def test_list_presets(self, tmp_path):
        (tmp_path / "a.yaml").write_text("name: preset_a\nagents: []\ndag: {}")
        (tmp_path / "b.yaml").write_text("name: preset_b\nagents: []\ndag: {}")
        (tmp_path / "c.yml").write_text("name: preset_c\nagents: []\ndag: {}")

        presets = list_presets(tmp_path)
        assert len(presets) == 3
        names = {p.name for p in presets}
        assert "preset_a" in names
        assert "preset_b" in names
        assert "preset_c" in names

    def test_list_presets_nonexistent_dir(self, tmp_path):
        presets = list_presets(tmp_path / "nonexistent")
        assert presets == []


# ── SwarmRuntime Tests ─────────────────────────────────────


class TestSwarmRuntime:
    def test_execute_simple_dag(self, tmp_path):
        preset = SwarmPreset(
            name="simple",
            agents=[
                AgentCall(agent_name="a1", prompt=""),
                AgentCall(agent_name="a2", prompt="", context={"input_from": ["a1"]}),
            ],
            dag={"a1": [], "a2": ["a1"]},
        )

        runtime = SwarmRuntime()
        result = runtime.execute(preset, tmp_path, "test task")

        assert result.success
        assert result.preset_name == "simple"
        assert len(result.agent_results) == 2
        assert result.agent_results["a1"].status == AgentStatus.SUCCESS
        assert result.agent_results["a2"].status == AgentStatus.SUCCESS

    def test_execute_parallel_layer(self, tmp_path):
        preset = SwarmPreset(
            name="parallel",
            agents=[
                AgentCall(agent_name="a1", prompt=""),
                AgentCall(agent_name="a2", prompt=""),
                AgentCall(agent_name="a3", prompt="", context={"input_from": ["a1", "a2"]}),
            ],
            dag={"a1": [], "a2": [], "a3": ["a1", "a2"]},
        )

        runtime = SwarmRuntime()
        result = runtime.execute(preset, tmp_path, "test task")

        assert result.success
        assert len(result.agent_results) == 3

    def test_execute_with_failure(self, tmp_path):
        preset = SwarmPreset(
            name="failing",
            agents=[
                AgentCall(agent_name="a1", prompt=""),
            ],
            dag={"a1": []},
        )

        mock_controller = MagicMock()
        mock_controller.execute_agent.side_effect = RuntimeError("agent failed")

        runtime = SwarmRuntime(controller=mock_controller)
        result = runtime.execute(preset, tmp_path, "test task")

        assert not result.success
        assert result.agent_results["a1"].status == AgentStatus.ERROR

    def test_cancel(self, tmp_path):
        runtime = SwarmRuntime()
        runtime._active_runs["test_run"] = True

        ok = runtime.cancel("test_run")
        assert ok
        assert "test_run" not in runtime._active_runs

    def test_cancel_nonexistent(self):
        runtime = SwarmRuntime()
        ok = runtime.cancel("nonexistent")
        assert not ok

    def test_topological_layers(self):
        runtime = SwarmRuntime()
        dag = {
            "a": [],
            "b": ["a"],
            "c": ["a"],
            "d": ["b", "c"],
        }
        layers = runtime._topological_layers(dag)
        assert len(layers) == 3
        assert layers[0] == ["a"]
        assert set(layers[1]) == {"b", "c"}
        assert layers[2] == ["d"]

    def test_topological_layers_cycle(self):
        runtime = SwarmRuntime()
        dag = {"a": ["b"], "b": ["a"]}
        layers = runtime._topological_layers(dag)
        # Cycle detection: remaining nodes added as single layer
        assert len(layers) == 1

    def test_gather_upstream(self):
        runtime = SwarmRuntime()
        results = {
            "a1": AgentResult(agent_id="a1", status=AgentStatus.SUCCESS, output="out1"),
            "a2": AgentResult(agent_id="a2", status=AgentStatus.ERROR, output=""),
        }
        upstream = runtime._gather_upstream("a3", {"a3": ["a1", "a2"]}, results)
        assert "a1" in upstream
        assert "a2" not in upstream  # failed agents excluded


# ── SwarmResult Tests ──────────────────────────────────────


class TestSwarmResult:
    def test_default_values(self):
        result = SwarmResult()
        assert result.run_id == ""
        assert result.preset_name == ""
        assert result.agent_results == {}
        assert result.elapsed_s == 0.0
        assert not result.success

    def test_agent_result_default(self):
        ar = AgentResult(agent_id="test")
        assert ar.agent_id == "test"
        assert ar.status == AgentStatus.PENDING
        assert ar.output == ""
        assert ar.error is None
