"""W-split: LangGraph round execution for AutoresearchRunner."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .graph import StudyGraph

logger = logging.getLogger(__name__)

class RunnerLangGraphMixin:
    def _load_graph(self, path: Path, sid: str) -> "StudyGraph":
        """Load the study's persisted execution graph.

        Falls back to ``DEFAULT_STANDARD_GRAPH`` when ``graph.json`` is
        missing or malformed (legacy studies pre-migration). Validates
        and logs warnings; never raises — a broken graph must not block
        a running study.
        """
        from .graph import StudyGraph
        from .graph_templates import DEFAULT_STANDARD_GRAPH

        graph = StudyGraph.load(path, sid)
        if graph is None:
            graph = DEFAULT_STANDARD_GRAPH
        errors = graph.validate()
        if errors:
            logger.warning(
                "Study %s graph validation warnings: %s; "
                "falling back to standard template",
                sid, errors,
            )
            graph = DEFAULT_STANDARD_GRAPH
        return graph

    def _run_round_via_langgraph(
        self,
        path: Path,
        strategy: str,
        current_state: dict,
        run_dir: Path,
        graph: "StudyGraph",
        *,
        session: str,
        sid: str,
        round_num: int,
        directive_text: str | None,
    ) -> dict:
        """Execute one round via LangGraph engine.

        Requires ``langgraph`` extra: ``pip install strategy-research[langgraph]``.
        Falls back to phase engine if langgraph is not installed.

        P4: HITL is enabled by default for langgraph engine. The novelty
        gate interrupt pauses execution for human approval of the
        researcher's hypothesis.
        """
        try:
            from .langgraph_engine import run_round_langgraph
        except ImportError:
            logger.warning(
                "langgraph extra not installed; falling back to phases engine. "
                "Install with: pip install strategy-research[langgraph]"
            )
            raise RuntimeError(
                "langgraph engine selected but langgraph package not installed. "
                "Install with: pip install strategy-research[langgraph]"
            )

        # P6: Determine profile from engine field
        profile_name = getattr(self._get_study(), "engine", None) or "langgraph"
        from .langgraph_engine import get_profile
        profile = get_profile(profile_name)

        return run_round_langgraph(
            runner=self,
            path=path,
            strategy=strategy,
            current_state=current_state,
            run_dir=run_dir,
            graph=graph,
            session=session,
            sid=sid,
            round_num=round_num,
            directive_text=directive_text,
            profile=profile,
        )

    async def _wait_for_approval(
        self, sid: str, round_num: int, timeout_s: int = 600
    ) -> str:
        """Poll for HITL approval.

        Returns the resolved decision: ``"approved"``, ``"rejected"``,
        ``"cancelled"`` (cancel/pause observed while waiting), or
        ``"timeout"`` when no response arrived within ``timeout_s``.
        """
        import asyncio
        start = time.time()
        while time.time() - start < timeout_s:
            # M1/L9: cancel (and pause) must cut the wait short — the old
            # loop slept through a cancel for up to the full timeout.
            if self.control.cancelled or self.control.paused:
                return "cancelled"
            interrupt = self.study_store.get_interrupt_for_round(sid, round_num)
            if interrupt is not None and interrupt.status in ("approved", "rejected"):
                return interrupt.status
            await asyncio.sleep(5)
        return "timeout"

    def _expire_interrupt(self, sid: str, round_num: int) -> None:
        """Mark the round's pending HITL interrupt expired (M1).

        Without this the interrupt row stayed ``pending`` forever after a
        timeout/rejection: the webui approval card reappeared on reload
        and a late answer was silently written to no consumer.
        """
        try:
            interrupt = self.study_store.get_pending_interrupt(sid, round_num)
            if interrupt is not None:
                self.study_store.respond_interrupt(interrupt.interrupt_id, "expired")
        except Exception:  # noqa: BLE001 — best-effort cleanup
            logger.warning(
                "failed to expire interrupt round=%s study=%s",
                round_num, sid, exc_info=True,
            )

    def _resume_round_langgraph(
        self,
        path: Path,
        strategy: str,
        current_state: dict,
        run_dir: Path,
        graph: "StudyGraph",
        *,
        session: str,
        sid: str,
        round_num: int,
        directive_text: str | None,
    ) -> dict:
        """Resume a HITL-paused round from checkpoint."""
        try:
            from .langgraph_engine import resume_round_langgraph
        except ImportError:
            raise RuntimeError("langgraph package not installed")

        # Same profile resolution as _run_round_via_langgraph — the
        # rebuilt graph must include the HITL gate node the checkpoint
        # stopped at, otherwise resume fails or silently skips gating.
        profile_name = getattr(self._get_study(), "engine", None) or "langgraph"
        from .langgraph_engine import get_profile
        profile = get_profile(profile_name)

        return resume_round_langgraph(
            runner=self,
            path=path,
            strategy=strategy,
            current_state=current_state,
            run_dir=run_dir,
            graph=graph,
            session=session,
            sid=sid,
            round_num=round_num,
            directive_text=directive_text,
            profile=profile,
        )

