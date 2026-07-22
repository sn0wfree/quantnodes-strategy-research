"""End-to-end tests for the goal subsystem (P3-a).

These tests simulate the full lifecycle of a research goal:
  1. start a goal
  2. add evidence per criterion
  3. write audit rows
  4. complete (with full verification)
  5. verify supersession when starting a new goal
  6. ensure LIVE_TRADING is rejected everywhere
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from strategy_research.core.goal import (
    AuditRow,
    EvidenceInput,
    GoalStatus,
    GoalStore,
    RiskTier,
    StaleGoalError,
    default_goal_criteria,
    format_goal_context,
    format_goal_continuation_prompt,
)


@pytest.fixture
def store(tmp_path: Path) -> GoalStore:
    return GoalStore(db_path=tmp_path / "e2e.db")


def _make_artifact(tmp_path: Path, name: str, content: str = "verified data") -> tuple[Path, str]:
    path = tmp_path / name
    path.write_text(content)
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


class TestGoalLifecycle:
    """The full start → evidence → audit → complete flow."""

    def test_full_lifecycle(self, store: GoalStore, tmp_path: Path):
        # 1. Start a goal
        goal = store.replace_goal(
            session_id="sess_lifecycle",
            objective="Investigate momentum factor in A-shares",
            criteria=default_goal_criteria(),
        )
        assert goal.status is GoalStatus.ACTIVE

        # 2. Add evidence for each criterion (verified via artifacts)
        criteria = store.list_criteria(goal.goal_id)
        evidence_ids = []
        for i, crit in enumerate(criteria):
            artifact, digest = _make_artifact(tmp_path, f"data_{i}.csv")
            ev = store.append_evidence(
                session_id="sess_lifecycle",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                evidence=EvidenceInput(
                    text=f"evidence for {crit.text}",
                    criterion_id=crit.criterion_id,
                    artifact_path=str(artifact),
                    artifact_hash=digest,
                    symbol_universe=["000300.SH"],
                    benchmark=["SPY"],
                    timeframe="2020-2024",
                    confidence="high",
                ),
            )
            evidence_ids.append(ev.evidence_id)

        # 3. Write audit rows
        audit_rows = [
            AuditRow(
                criterion_id=crit.criterion_id,
                result="satisfied",
                evidence_ids=[evid],
                notes=f"verified by artifact",
            )
            for crit, evid in zip(criteria, evidence_ids)
        ]

        # 4. Complete
        completed = store.update_status(
            session_id="sess_lifecycle",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            status=GoalStatus.COMPLETE,
            audit=audit_rows,
            recap="All criteria verified via backtest artifacts",
        )
        assert completed.status is GoalStatus.COMPLETE
        assert completed.completed_at is not None
        assert completed.recap is not None
        assert "verified" in completed.recap

        # 5. After completion, no current goal for this session
        assert store.get_current_goal("sess_lifecycle") is None

    def test_context_block_after_lifecycle(self, store: GoalStore, tmp_path: Path):
        """format_goal_context after completion shows COMPLETE status."""
        goal = store.replace_goal(
            session_id="sess_ctx",
            objective="Test",
            criteria=["crit only"],
        )
        criteria = store.list_criteria(goal.goal_id)
        artifact, digest = _make_artifact(tmp_path, "x.csv")
        ev = store.append_evidence(
            session_id="sess_ctx",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(text="e", criterion_id=criteria[0].criterion_id,
                                   artifact_path=str(artifact), artifact_hash=digest),
        )
        store.update_status(
            session_id="sess_ctx",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            status=GoalStatus.COMPLETE,
            audit=[AuditRow(criterion_id=criteria[0].criterion_id,
                            result="satisfied", evidence_ids=[ev.evidence_id])],
        )
        snap = store.get_goal_snapshot(goal.goal_id)
        assert snap["goal"]["status"] == "complete"
        ctx = format_goal_context(snap)
        assert "status: complete" in ctx

    def test_supersession_chain(self, store: GoalStore):
        """Replacing a goal three times produces a chain of superseded ones."""
        ids = []
        for i in range(3):
            g = store.replace_goal(
                session_id="sess_chain",
                objective=f"objective {i}",
                criteria=["x"],
            )
            ids.append(g.goal_id)
        current = store.get_current_goal("sess_chain")
        assert current.goal_id == ids[2]
        for old_id in ids[:2]:
            old = store.get_goal(old_id)
            assert old is not None
            assert old.status is GoalStatus.SUPERSEDED


class TestLiveTradingDefense:
    """LIVE_TRADING_OR_EXECUTION is rejected at every entry point."""

    def test_replace_goal_rejects_live_objective(self, store: GoalStore):
        with pytest.raises(ValueError, match="live trading"):
            store.replace_goal(
                session_id="s", objective="place order now", criteria=["x"],
            )

    def test_replace_goal_rejects_live_criterion(self, store: GoalStore):
        with pytest.raises(ValueError, match="live trading"):
            store.replace_goal(
                session_id="s",
                objective="research x",
                criteria=["立即下单"],
            )

    def test_replace_goal_rejects_live_risk_tier(self, store: GoalStore):
        with pytest.raises(ValueError, match="live trading"):
            store.replace_goal(
                session_id="s",
                objective="research",
                criteria=["x"],
                risk_tier=RiskTier.LIVE_TRADING_OR_EXECUTION,
            )

    def test_update_goal_rejects_live_objective(self, store: GoalStore):
        goal = store.replace_goal(session_id="s", objective="x", criteria=["y"])
        with pytest.raises(ValueError, match="live trading"):
            store.update_goal(
                session_id="s",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                objective="buy AAPL now",
            )


class TestConcurrencyAndIsolation:
    """Sessions are isolated; stale-write guard enforces per-session."""

    def test_sessions_are_isolated(self, store: GoalStore):
        g1 = store.replace_goal(session_id="alice", objective="a", criteria=["x"])
        g2 = store.replace_goal(session_id="bob", objective="b", criteria=["y"])
        assert g1.session_id == "alice"
        assert g2.session_id == "bob"
        alice_current = store.get_current_goal("alice")
        bob_current = store.get_current_goal("bob")
        assert alice_current.goal_id == g1.goal_id
        assert bob_current.goal_id == g2.goal_id

    def test_cannot_mutate_other_session_goal(self, store: GoalStore):
        g_alice = store.replace_goal(session_id="alice", objective="a", criteria=["x"])
        with pytest.raises(StaleGoalError):
            store.update_goal(
                session_id="bob",
                goal_id=g_alice.goal_id,
                expected_goal_id=g_alice.goal_id,
                objective="hijack",
            )

    def test_delete_session_isolated(self, store: GoalStore):
        store.replace_goal(session_id="alice", objective="a", criteria=["x"])
        store.replace_goal(session_id="bob", objective="b", criteria=["y"])
        deleted = store.delete_session_goals("alice")
        assert deleted >= 1
        assert store.get_current_goal("alice") is None
        assert store.get_current_goal("bob") is not None


class TestSnapshotJsonSerialization:
    """Snapshot must be JSON-safe (no NaN, no Enum, no Path)."""

    def test_full_snapshot_round_trip(self, store: GoalStore, tmp_path: Path):
        goal = store.replace_goal(
            session_id="sess",
            objective="round trip",
            criteria=["a", "b"],
        )
        artifact, digest = _make_artifact(tmp_path, "x.txt")
        criteria = store.list_criteria(goal.goal_id)
        store.append_evidence(
            session_id="sess",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(text="e", criterion_id=criteria[0].criterion_id,
                                   artifact_path=str(artifact), artifact_hash=digest),
        )
        snap = store.get_goal_snapshot(goal.goal_id)
        encoded = json.dumps(snap, ensure_ascii=False, default=str)
        decoded = json.loads(encoded)
        assert decoded["goal"]["goal_id"] == goal.goal_id
        assert decoded["goal"]["status"] == "active"
        assert decoded["goal"]["risk_tier"] == "research_general"
        assert len(decoded["criteria"]) == 2
        assert decoded["evidence_count"] == 1
        # RiskTier/GoalStatus serialized as plain strings, not Enum
        assert isinstance(decoded["goal"]["status"], str)
        assert isinstance(decoded["goal"]["risk_tier"], str)


class TestContinuationPromptE2E:
    """Continuation prompt reflects actual ledger state."""

    def test_continuation_lists_only_open_criteria(self, store: GoalStore, tmp_path: Path):
        goal = store.replace_goal(
            session_id="sess",
            objective="x",
            criteria=["open item", "to cover", "another"],
        )
        criteria = store.list_criteria(goal.goal_id)
        # Cover only the second one
        artifact, digest = _make_artifact(tmp_path, "x.csv")
        store.append_evidence(
            session_id="sess",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(
                text="e",
                criterion_id=criteria[1].criterion_id,
                artifact_path=str(artifact),
                artifact_hash=digest,
            ),
        )
        snap = store.get_goal_snapshot(goal.goal_id)
        prompt = format_goal_continuation_prompt(snap)
        assert "open item" in prompt
        assert "another" in prompt
        # The "to cover" criterion was covered, so its text shouldn't appear in open_required_items
        open_section = prompt.split("open_required_items:")[1].split("Rules:")[0]
        assert "to cover" not in open_section