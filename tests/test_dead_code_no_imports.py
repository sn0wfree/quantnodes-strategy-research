"""Regression tests ensuring deleted components are not imported
by any live code. Run this after major cleanup batches.

These tests walk the source tree and verify that no surviving file
imports any of the deleted modules. If a developer accidentally
re-introduces an import of a deleted file, these tests catch it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

# Deleted components that should never be imported again
DELETED_MODULES = {
    # Cluster A — three-view remnants
    "buildAgentTraces",
    "agentTraceTypes",
    "AgentCardView",
    "TimelineView",
    # Cluster B — dashboard subsystem
    "DashboardGrid",
    "WidgetCard",
    "WidgetPicker",
    "studyDashboard",
    "LiveActivity",
    "EventTimeline",
    "KnowledgeView",
    "TodosView",
    "JournalView",
    "RoundCard",
    "KeyPointsPanel",
    "StudyDirectiveComposer",
    "AgentChatLog",
    # Cluster C — legacy layout
    "StudyTab",
    "StudyProgress",
    "StudyFlowTab",
    "FlowCard",
    "ObjectiveProgress",
    "StudyObjectiveHistory",
    "ScoreboardMini",
    "MetricsTrendChart",
    "BudgetBar",
    "AgentFlowCanvas",
    "AgentNodeCard",
    "AgentNodeDetail",
    "RoundPicker",
    "DAGVisualization",
    "AgentActivityPanel",
    "SearchPanel",
}


def _find_imports(filepath: Path) -> set[str]:
    """Extract all import names from a Python file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _find_ts_imports(filepath: Path) -> set[str]:
    """Extract TypeScript import paths from a file."""
    imports = set()
    for line in filepath.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("import ") and not stripped.startswith("} from "):
            continue
        # Simple heuristic: extract quoted paths
        for part in stripped.split("'"):
            if "/" in part and "." in part:
                imports.add(part)
    return imports


# ── Python tests ──────────────────────────────────────────────

@pytest.mark.parametrize("module", sorted(DELETED_MODULES))
def test_no_python_import_of_deleted_module(module: str) -> None:
    """No .py file in src/ should import a deleted module."""
    src_files = list(SRC.rglob("*.py"))
    violations = []
    for fp in src_files:
        imports = _find_imports(fp)
        for imp in imports:
            if module in imp:
                violations.append(str(fp.relative_to(SRC)))
    if violations:
        msg = (
            f"Deleted module {module!r} is still imported by:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )
        pytest.fail(msg)


# ── TypeScript tests (checked via pytest to keep in one suite) ──

TS_SRC = Path(__file__).parent.parent / "src"  # webui/frontend/src


@pytest.mark.parametrize("module", sorted(DELETED_MODULES))
def test_no_ts_import_of_deleted_module(module: str) -> None:
    """No .ts/.tsx file in frontend src/ should import a deleted module."""
    ts_files = list(TS_SRC.rglob("*.ts")) + list(TS_SRC.rglob("*.tsx"))
    violations = []
    for fp in ts_files:
        try:
            for line in fp.read_text(encoding="utf-8").splitlines():
                if module in line and ("from '" in line or 'from "' in line):
                    violations.append(str(fp.relative_to(TS_SRC)))
                    break
        except UnicodeDecodeError:
            pass
    if violations:
        msg = (
            f"Deleted module {module!r} is still imported by:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )
        pytest.fail(msg)
