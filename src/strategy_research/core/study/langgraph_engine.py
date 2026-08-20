"""LangGraph engine for study round execution.

Requires ``langgraph`` extra: ``pip install strategy-research[langgraph]``.

This module is imported lazily by ``AutoresearchRunner._run_round_via_langgraph``
only when the study's ``engine`` field is set to ``langgraph``.  If the
``langgraph`` package is not installed, the import fails and the runner
falls back with a clear error message.

P0: Placeholder — full implementation in P1.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def run_round_langgraph(
    runner: Any,
    path: Path,
    strategy: str,
    current_state: dict,
    run_dir: Path,
    graph: Any,
    *,
    session: str,
    sid: str,
    round_num: int,
    directive_text: str | None,
) -> dict:
    """Execute one round using the LangGraph engine.

    P0 placeholder: raises NotImplementedError.
    P1 will implement: StudyGraph → StateGraph conversion, AgentExecutor
    nodes, serial layer execution, legacy schema output.
    """
    raise NotImplementedError(
        "LangGraph engine is not yet implemented (P1). "
        "Use engine='phases' or engine='dag' for now."
    )
