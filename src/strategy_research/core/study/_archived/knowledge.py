"""Knowledge collection and evidence recording utilities.

Extracted from runner.py to reduce file size and improve testability.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def collect_knowledge(
    goal_store: Any,
    study: Any,
    topics: list[str],
) -> int:
    """Collect knowledge for the given topics. Returns count added."""
    if not study.goal_id or not topics:
        return 0
    try:
        # Use the study_reviewer agent to collect knowledge
        from strategy_research.core.agent.builtin_plugins import get_knowledge_collector_plugin
        from strategy_research.core.agent.executor import AgentExecutor
        from strategy_research.core.agent.registry import get_default_registry

        registry = get_default_registry()
        executor = AgentExecutor(registry)
        plugin = get_knowledge_collector_plugin()
        if plugin is None:
            return 0

        workspace = Path(study.workspace_path).resolve()
        task = f"Collect knowledge on these topics: {', '.join(topics)}"
        result = executor.execute(plugin, task, workspace)

        if result.status != "success" or not result.output:
            return 0

        # Parse and append to knowledge.md
        knowledge_path = workspace / "study" / study.study_id / "knowledge.md"
        existing = knowledge_path.read_text(encoding="utf-8") if knowledge_path.exists() else ""

        entries = []
        try:
            data = json.loads(result.output) if isinstance(result.output, str) else result.output
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict) and "entries" in data:
                entries = data["entries"]
        except (json.JSONDecodeError, TypeError):
            pass

        if not entries:
            return 0

        new_lines = []
        for entry in entries:
            text = entry.get("text", "") if isinstance(entry, dict) else str(entry)
            if text and text not in existing:
                new_lines.append(f"- {text}")

        if new_lines:
            with open(knowledge_path, "a", encoding="utf-8") as f:
                f.write("\n".join(new_lines) + "\n")

        return len(new_lines)
    except Exception as exc:
        logger.warning("knowledge collection failed: %s", exc)
        return 0


def record_keep_evidence(
    goal_store: Any,
    study: Any,
    round_num: int,
    run_name: str,
    metrics: dict,
) -> None:
    """Record evidence for a keep verdict in the goal journal."""
    if not study.goal_id:
        return
    try:
        from strategy_research.core.goal import EvidenceInput

        criteria = goal_store.list_criteria(study.goal_id)
        for c in criteria:
            if not c.required:
                continue
            goal_store.append_evidence(
                session_id=study.session_id,
                goal_id=study.goal_id,
                expected_goal_id=study.goal_id,
                evidence=EvidenceInput(
                    text=f"Round {round_num} keep — {run_name}: Calmar={metrics.get('calmar')} Sharpe={metrics.get('sharpe')}",
                    criterion_id=c.criterion_id,
                    evidence_type="metric_target",
                    run_id=run_name,
                    source_provider="study",
                    source_type="round_keep",
                ),
            )
    except Exception as exc:
        logger.warning("record_keep_evidence failed: %s", exc)
