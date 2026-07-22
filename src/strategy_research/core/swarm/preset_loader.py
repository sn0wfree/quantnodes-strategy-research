"""Preset loader — load swarm presets from YAML files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..workflow.types import AgentCall
from .runtime import SwarmPreset

logger = logging.getLogger(__name__)


def load_preset(path: Path) -> SwarmPreset | None:
    """Load a swarm preset from a YAML file.

    Format:
        name: quant_research_team
        description: 量化研究团队
        agents:
          - id: researcher
            prompt_file: .prompts/researcher.md
            tools: [read_file, compute_factor]
          - id: factor_analyst
            prompt_file: .prompts/factor_analyst.md
            input_from: [researcher]
            tools: [compute_factor, run_backtest]
        dag:
          researcher: []
          factor_analyst: [researcher]
    """
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed, cannot load presets")
        return None

    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load preset %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("Invalid preset format in %s", path)
        return None

    name = data.get("name", path.stem)
    description = data.get("description", "")

    # Parse agents
    agents: list[AgentCall] = []
    for agent_data in data.get("agents", []):
        agent_id = agent_data.get("id", "")
        prompt_file = agent_data.get("prompt_file", "")
        tools = agent_data.get("tools", [])
        input_from = agent_data.get("input_from", [])

        agents.append(AgentCall(
            agent_name=agent_id,
            prompt=prompt_file,
            context={"tools": tools, "input_from": input_from},
        ))

    # Parse DAG
    dag: dict[str, list[str]] = {}
    for agent_id, deps in data.get("dag", {}).items():
        dag[agent_id] = list(deps) if deps else []

    return SwarmPreset(
        name=name,
        description=description,
        agents=agents,
        dag=dag,
    )


def list_presets(directory: Path) -> list[SwarmPreset]:
    """List all presets in a directory."""
    presets: list[SwarmPreset] = []
    if not directory.is_dir():
        return presets

    for yml_file in sorted(directory.glob("*.yaml")):
        preset = load_preset(yml_file)
        if preset is not None:
            presets.append(preset)

    for yml_file in sorted(directory.glob("*.yml")):
        preset = load_preset(yml_file)
        if preset is not None:
            presets.append(preset)

    return presets
