"""Tests for the study task system: models + store.

Covers StudyRecord / StudyStatus lifecycle, StudyStore CRUD, validation
guardrails, active-study lookup and startup recovery scanning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core.study import (
    ACTIVE_EXECUTION_STATUSES,
    StudyRecord,
    StudyStatus,
    StudyStore,
    default_metric_targets,
)
from strategy_research.core.study.models import MetricTarget


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "goal_study_test.db"


@pytest.fixture
def store(db_path: Path) -> StudyStore:
    with StudyStore(db_path=db_path) as s:
        yield s


@pytest.fixture
def session_id() -> str:
    return "sess-study-1"


@pytest.fixture
def make_study(store: StudyStore, session_id: str):
    created = []

    def _make(**overrides):
        kw = dict(
            session_id=session_id,
            goal_id="goal_demo",
            objective="研究动量因子",
            workspace_path="/tmp/ws",
            strategy_name="rot_alpha",
        )
        kw.update(overrides)
        r = store.create_study(**kw)
        created.append(r.study_id)
        return r

    return _make


# ── models ───────────────────────────────────────────────────────────


class TestStudyModels:
    def test_status_values(self):
        assert StudyStatus.QUEUED.value == "queued"
        assert StudyStatus.RUNNING.value == "running"
        assert StudyStatus.COMPLETE.value == "complete"
        assert StudyStatus.BUDGET_LIMITED.value == "budget_limited"

    def test_active_set_excludes_terminals(self):
        assert StudyStatus.COMPLETE not in ACTIVE_EXECUTION_STATUSES
        assert StudyStatus.CANCELLED not in ACTIVE_EXECUTION_STATUSES
        assert StudyStatus.ERROR not in ACTIVE_EXECUTION_STATUSES
        assert StudyStatus.RUNNING in ACTIVE_EXECUTION_STATUSES

    def test_metric_target_serializes(self):
        m = MetricTarget(name="calmar", op=">=", value=0.5)
        assert m.as_dict() == {"name": "calmar", "op": ">=", "value": 0.5}

    def test_default_metric_targets_match_acceptance(self):
        t = default_metric_targets()
        names = {x["name"] for x in t}
        assert names == {"calmar", "sharpe", "max_dd"}
        ops = {x["name"]: x["op"] for x in t}
        assert ops["calmar"] == ">="
        assert ops["max_dd"] == ops["max_dd"]  # check
        vals = {x["name"]: x["value"] for x in t}
        assert vals["calmar"] == 0.5
        assert vals["sharpe"] == 0.3
        assert vals["max_dd"] == -0.15

    def test_record_is_frozen(self):
        r = StudyRecord(
            study_id="s", session_id="se", goal_id=None, objective="o",
            executor_type="autoresearch", workspace_path="/w",
            strategy_name="st",
        )
        with pytest.raises(Exception):
            r.study_id = "other"  # type: ignore[misc]


# ── store CRUD ───────────────────────────────────────────────────────


class TestStudyStoreCRUD:
    def test_create_defaults(self, make_study):
        r = make_study()
        assert r.study_id.startswith("study_")
        assert r.execution_status == StudyStatus.QUEUED
        assert r.executor_type == "autoresearch"
        assert r.current_round == 0
        assert r.heartbeat
        assert r.created_at
        assert r.completed_at is None
        assert r.max_rounds is None
        assert r.behavior is None

    def test_create_with_targets(self, store: StudyStore, session_id: str):
        targets = [{"name": "calmar", "op": ">=", "value": 0.7}]
        r = store.create_study(
            session_id=session_id, goal_id="g1", objective="xxx",
            workspace_path="/w", strategy_name="s",
            metric_targets=targets, max_rounds=5, budget_token=10000,
            behavior="improving",
        )
        assert r.metric_targets == targets
        assert r.max_rounds == 5
        assert r.budget_token == 10000
        assert r.behavior == "improving"

    def test_get_missing_returns_none(self, store: StudyStore):
        assert store.get_study("does_not_exist") is None

    def test_get_active_study_none_when_no_rows(
        self, store: StudyStore, session_id: str
    ):
        assert store.get_active_study(session_id) is None

    def test_get_active_study_oldest_first(self, store: StudyStore, session_id: str):
        a = store.create_study(session_id=session_id, goal_id="g",
                               objective="a", workspace_path="/w", strategy_name="s")
        b = store.create_study(session_id=session_id, goal_id="g",
                               objective="b", workspace_path="/w", strategy_name="s")
        # both queued; active should be oldest
        assert store.get_active_study(session_id).study_id == a.study_id
        # complete first; second still queued → now active
        store.update_execution_status(a.study_id, StudyStatus.COMPLETE)
        assert store.get_active_study(session_id).study_id == b.study_id
        store.update_execution_status(b.study_id, StudyStatus.CANCELLED)
        assert store.get_active_study(session_id) is None

    def test_list_filters_session(self, store: StudyStore):
        store.create_study(session_id="se1", goal_id=None, objective="x",
                           workspace_path="/w", strategy_name="s")
        store.create_study(session_id="se2", goal_id=None, objective="y",
                           workspace_path="/w", strategy_name="s")
        assert len(store.list_studies(session_id="se1")) == 1
        assert len(store.list_studies(session_id="se2")) == 1
        assert len(store.list_studies()) == 2

    def test_list_filters_status(self, make_study, store: StudyStore, session_id: str):
        a = make_study()
        store.update_execution_status(a.study_id, StudyStatus.RUNNING)
        make_study()  # queued
        assert len(store.list_studies(status=StudyStatus.RUNNING)) == 1
        assert len(store.list_studies(status=StudyStatus.QUEUED)) == 1
        assert len(store.list_studies(status=StudyStatus.COMPLETE)) == 0

    def test_list_newest_first(self, store: StudyStore, session_id: str):
        ids = []
        for i in range(3):
            r = store.create_study(session_id=session_id, goal_id=None,
                                   objective=f"obj-{i}", workspace_path="/w",
                                   strategy_name="s")
            ids.append(r.study_id)
        listed = [r.study_id for r in store.list_studies(session_id=session_id)]
        assert listed == list(reversed(ids))  # DESC

    def test_update_status_terminal_sets_completed_at(
        self, make_study, store: StudyStore
    ):
        r = make_study()
        pre = store.get_study(r.study_id).completed_at
        assert pre is None
        upd = store.update_execution_status(r.study_id, StudyStatus.COMPLETE)
        assert upd is not None
        assert upd.completed_at is not None
        assert upd.completed_at != ""

    def test_update_status_running_keeps_completed_at_null(
        self, make_study, store: StudyStore
    ):
        r = make_study()
        upd = store.update_execution_status(r.study_id, StudyStatus.RUNNING)
        assert upd is not None
        assert upd.completed_at is None

    def test_update_status_with_metrics_and_error(
        self, make_study, store: StudyStore
    ):
        r = make_study()
        upd = store.update_execution_status(
            r.study_id, StudyStatus.ERROR,
            last_error="boom",
            last_metrics={"calmar": 0.2},
            last_verdict="discard",
        )
        assert upd.last_error == "boom"
        assert upd.last_metrics == {"calmar": 0.2}
        assert upd.last_verdict == "discard"

    def test_update_round_heartbeat(self, make_study, store: StudyStore):
        r = make_study()
        assert store.get_study(r.study_id).current_round == 0
        store.update_round_heartbeat(r.study_id, 7)
        assert store.get_study(r.study_id).current_round == 7
        assert store.get_study(r.study_id).heartbeat != ""

    def test_update_last_metrics(self, make_study, store: StudyStore):
        r = make_study()
        store.update_last_metrics(
            r.study_id, {"calmar": 0.55, "sharpe": 0.4}, "keep"
        )
        got = store.get_study(r.study_id)
        assert got.last_metrics == {"calmar": 0.55, "sharpe": 0.4}
        assert got.last_verdict == "keep"

    def test_list_active_studies_recovery_scan(self, store: StudyStore):
        r1 = store.create_study(session_id="se1", goal_id=None, objective="a",
                                 workspace_path="/w", strategy_name="s")
        r2 = store.create_study(session_id="se2", goal_id=None, objective="b",
                                 workspace_path="/w", strategy_name="s")
        store.update_execution_status(r1.study_id, StudyStatus.RUNNING)
        store.update_execution_status(r2.study_id, StudyStatus.QUEUED)
        # complete one
        store.update_execution_status(
            store.create_study(session_id="se3", goal_id=None, objective="c",
                               workspace_path="/w", strategy_name="s").study_id,
            StudyStatus.COMPLETE,
        )
        actives = store.list_active_studies()
        ids = {s.study_id for s in actives}
        assert r1.study_id in ids
        assert r2.study_id in ids
        assert len(actives) == 2

    def test_delete_session_studies(self, store: StudyStore):
        store.create_study(session_id="se1", goal_id=None, objective="a",
                           workspace_path="/w", strategy_name="s")
        store.create_study(session_id="se1", goal_id=None, objective="b",
                           workspace_path="/w", strategy_name="s")
        store.create_study(session_id="se2", goal_id=None, objective="c",
                           workspace_path="/w", strategy_name="s")
        assert store.delete_session_studies("se1") == 2
        assert len(store.list_studies(session_id="se1")) == 0
        assert len(store.list_studies(session_id="se2")) == 1

    def test_delete_session_studies_empty(self, store: StudyStore, session_id: str):
        # empty session_id is a misuse guard
        with pytest.raises(ValueError):
            store.delete_session_studies("")

    def test_goal_id_nullable(self, store: StudyStore, session_id: str):
        r = store.create_study(session_id=session_id, goal_id=None,
                               objective="x", workspace_path="/w", strategy_name="s")
        assert r.goal_id is None
        assert store.get_study(r.study_id).goal_id is None

    def test_persistence_across_connections(self, db_path: Path, session_id: str):
        with StudyStore(db_path=db_path) as s:
            r = s.create_study(session_id=session_id, goal_id="g", objective="x",
                               workspace_path="/w", strategy_name="s")
        # Reopen: schema + data survive
        with StudyStore(db_path=db_path) as s2:
            assert s2.get_study(r.study_id) is not None
            assert s2.get_active_study(session_id).study_id == r.study_id


# ── store validation ─────────────────────────────────────────────────


class TestStudyStoreValidation:
    @pytest.mark.parametrize("field,override", [
        ("session_id", "  "),
        ("objective", "  "),
        ("workspace_path", "  "),
        ("strategy_name", "  "),
    ])
    def test_empty_required(self, store: StudyStore, field, override, session_id):
        base = dict(
            session_id="s", goal_id=None, objective="x",
            workspace_path="/w", strategy_name="s",
        )
        base[field] = override
        with pytest.raises(ValueError):
            store.create_study(**base)

    def test_invalid_executor_type(self, store: StudyStore, session_id: str):
        with pytest.raises(ValueError):
            store.create_study(session_id=session_id, goal_id=None, objective="x",
                               workspace_path="/w", strategy_name="s",
                               executor_type="bogus")

    @pytest.mark.parametrize("name", ["cooldown_base", "cooldown_jitter", "min_cooldown"])
    def test_nonpositive_cooldown(self, store: StudyStore, session_id, name):
        with pytest.raises(ValueError):
            store.create_study(session_id=session_id, goal_id=None, objective="x",
                               workspace_path="/w", strategy_name="s",
                               **{name: 0})

    @pytest.mark.parametrize("name", ["budget_token", "budget_turn",
                                       "budget_time_seconds", "max_rounds"])
    def test_nonpositive_optional_budget(self, store: StudyStore, session_id, name):
        with pytest.raises(ValueError):
            store.create_study(session_id=session_id, goal_id=None, objective="x",
                               workspace_path="/w", strategy_name="s",
                               **{name: -1})