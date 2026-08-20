"""File I/O utilities for study execution.

Extracted from runner.py to reduce file size and improve testability.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def safe_json_loads(text: str, fallback: Any = None) -> Any:
    """Parse JSON with markdown fence stripping."""
    if not isinstance(text, str):
        return text if fallback is None else fallback
    text = text.strip()
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    for pattern in (
        r"```json\s*\n?(.*?)\n?\s*```",
        r"```\s*\n?(.*?)\n?\s*```",
        r"(\[.*\])",
        r"(\{.*\})",
    ):
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            continue
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
    return fallback


def build_round_task_text(state: dict, directive_text: str | None) -> str:
    """Compose the base task text for every agent in the round."""
    parts = []
    # Core context
    if state.get("objective"):
        parts.append(f"Objective: {state['objective']}")
    if state.get("strategy_py"):
        parts.append(f"Current strategy:\n{state['strategy_py']}")
    if state.get("results_tsv_tail"):
        parts.append(f"Recent results:\n{state['results_tsv_tail']}")
    # Review context
    if state.get("journal_context"):
        parts.append(f"Journal context:\n{state['journal_context']}")
    if state.get("lever_scoreboard"):
        parts.append(f"Lever scoreboard:\n{state['lever_scoreboard']}")
    # Guidance
    if state.get("human_guidance"):
        parts.append(f"Human guidance:\n{state['human_guidance']}")
    # Directives
    if directive_text:
        parts.append(directive_text)
    # Factor failures
    if state.get("factor_failures"):
        parts.append(f"Factor failures from previous round: {json.dumps(state['factor_failures'])}")
    return "\n\n".join(parts)


def save_agent_output(run_dir: Path, agent_id: str, result: Any) -> None:
    """Save agent execution result to disk."""
    agent_dir = run_dir / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    output_path = agent_dir / f"{agent_id}.json"
    try:
        record = {
            "agent": agent_id,
            "status": getattr(result, "status", "unknown"),
            "output": getattr(result, "output", ""),
            "error": getattr(result, "error", None),
            "elapsed_s": getattr(result, "elapsed_s", 0),
            "timestamp": getattr(result, "timestamp", ""),
        }
        output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to save agent output %s: %s", agent_id, exc)


def update_results_tsv(
    runs_dir: Path,
    run_name: str,
    verdict: str,
    *,
    round_num: int | None = None,
    results_tsv: Path | None = None,
) -> None:
    """In-place verdict update with (round, run) composite matching."""
    results_path = results_tsv or (runs_dir / "results.tsv")
    if not results_path.exists():
        return
    content = results_path.read_text(encoding="utf-8")
    lines = content.strip().split("\n")
    for i in range(len(lines) - 1, 0, -1):
        parts = lines[i].split("\t")
        if len(parts) < 12:
            continue
        row_run = parts[0]
        row_round = parts[13] if len(parts) >= 14 else ""
        if row_run != run_name:
            continue
        if round_num is not None and row_round != str(round_num):
            continue
        if len(parts) >= 12:
            parts[11] = verdict
            lines[i] = "\t".join(parts)
        break
    results_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def emit_topology(
    emit_fn: Any,
    session: str,
    sid: str,
    round_num: int,
    graph: Any,
    node_map: dict,
    agent_outputs: dict,
) -> None:
    """Emit SSE events for graph topology visualization."""
    for node in graph.nodes:
        if not node.enabled:
            continue
        status = "pending"
        if node.id in agent_outputs:
            output = agent_outputs[node.id]
            if isinstance(output, dict) and output.get("error"):
                status = "error"
            else:
                status = "done"
        emit_fn(session, "study_graph_node", {
            "study_id": sid,
            "round": round_num,
            "node_id": node.id,
            "node_type": node.type,
            "label": node.label,
            "status": status,
        })
