"""Tests for Scheduled Research (cron parser, models, store, executor, CLI)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.core.scheduled_research.cron_parser import (
    parse_cron,
    next_cron_trigger,
    validate_cron,
)
from strategy_research.core.scheduled_research.models import (
    JobStatus,
    ScheduledResearchJob,
)
from strategy_research.core.scheduled_research.store import ScheduledResearchStore
from strategy_research.core.scheduled_research.executor import ScheduledResearchExecutor


# ── Cron Parser Tests ───────────────────────────────────────


class TestCronParser:
    def test_parse_all_star(self):
        fields = parse_cron("* * * * *")
        assert len(fields.minutes) == 60
        assert len(fields.hours) == 24
        assert len(fields.days_of_month) == 31
        assert len(fields.months) == 12
        assert len(fields.days_of_week) == 7

    def test_parse_specific_values(self):
        fields = parse_cron("0 9 * * 1-5")
        assert fields.minutes == {0}
        assert fields.hours == {9}
        assert fields.days_of_week == {1, 2, 3, 4, 5}

    def test_parse_step(self):
        fields = parse_cron("*/15 * * * *")
        assert fields.minutes == {0, 15, 30, 45}

    def test_parse_range(self):
        fields = parse_cron("0 9-17 * * 1-5")
        assert fields.hours == set(range(9, 18))

    def test_parse_list(self):
        fields = parse_cron("0,30 * * * *")
        assert fields.minutes == {0, 30}

    def test_parse_invalid_field_count(self):
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("* * *")

    def test_parse_invalid_value(self):
        with pytest.raises(ValueError, match="out of bounds"):
            parse_cron("60 * * * *")

    def test_validate_cron_valid(self):
        assert validate_cron("0 2 * * *") is True

    def test_validate_cron_invalid(self):
        assert validate_cron("invalid") is False

    def test_next_cron_trigger(self):
        # "every day at 2:00 AM"
        trigger = next_cron_trigger("0 2 * * *")
        assert trigger > time.time()
        # Should be within next 24 hours
        assert trigger - time.time() < 86400


# ── Model Tests ─────────────────────────────────────────────


class TestScheduledResearchJob:
    def test_default_values(self):
        job = ScheduledResearchJob()
        assert job.id.startswith("job_")
        assert job.status == JobStatus.PENDING
        assert job.created_at > 0

    def test_to_dict_roundtrip(self):
        job = ScheduledResearchJob(
            workspace="/tmp/ws",
            strategy_name="momentum",
            cron="0 2 * * *",
        )
        data = job.to_dict()
        restored = ScheduledResearchJob.from_dict(data)
        assert restored.id == job.id
        assert restored.workspace == "/tmp/ws"
        assert restored.cron == "0 2 * * *"
        assert restored.status == JobStatus.PENDING

    def test_is_due(self):
        job = ScheduledResearchJob(next_run_at=time.time() - 10)
        assert job.is_due() is True

    def test_is_not_due_future(self):
        job = ScheduledResearchJob(next_run_at=time.time() + 3600)
        assert job.is_due() is False

    def test_is_due_running_not_due(self):
        job = ScheduledResearchJob(
            status=JobStatus.RUNNING,
            next_run_at=time.time() - 10,
        )
        assert job.is_due() is False

    def test_is_recurring_cron(self):
        job = ScheduledResearchJob(cron="0 * * * *")
        assert job.is_recurring() is True

    def test_is_recurring_interval(self):
        job = ScheduledResearchJob(interval_ms=60000)
        assert job.is_recurring() is True

    def test_is_not_recurring(self):
        job = ScheduledResearchJob()
        assert job.is_recurring() is False


# ── Store Tests ─────────────────────────────────────────────


class TestScheduledResearchStore:
    def test_load_empty(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        jobs = store.load()
        assert jobs == []

    def test_add_and_load(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        job = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        store.add(job)

        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0].id == job.id

    def test_get_by_id(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        job = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        store.add(job)

        found = store.get(job.id)
        assert found is not None
        assert found.workspace == "/ws"

    def test_get_missing(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        assert store.get("nonexistent") is None

    def test_update(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        job = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        store.add(job)

        job.strategy_name = "updated"
        store.update(job)

        found = store.get(job.id)
        assert found.strategy_name == "updated"

    def test_delete(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        job = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        store.add(job)

        ok = store.delete(job.id)
        assert ok
        assert store.get(job.id) is None

    def test_delete_missing(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        assert store.delete("nonexistent") is False

    def test_list_filter_workspace(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        store.add(ScheduledResearchJob(workspace="/ws1", strategy_name="s1"))
        store.add(ScheduledResearchJob(workspace="/ws2", strategy_name="s2"))
        store.add(ScheduledResearchJob(workspace="/ws1", strategy_name="s3"))

        filtered = store.list_jobs(workspace="/ws1")
        assert len(filtered) == 2

    def test_list_filter_status(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        j1 = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        j1.status = JobStatus.COMPLETED
        store.add(j1)
        store.add(ScheduledResearchJob(workspace="/ws", strategy_name="s2"))

        filtered = store.list_jobs(status=JobStatus.COMPLETED)
        assert len(filtered) == 1

    def test_recover_stale_running(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        j1 = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        j1.status = JobStatus.RUNNING
        store.add(j1)

        count = store.recover_stale_running()
        assert count == 1

        found = store.get(j1.id)
        assert found.status == JobStatus.PENDING

    def test_atomic_write(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        store.add(ScheduledResearchJob(workspace="/ws", strategy_name="s1"))

        # Verify file exists and is valid JSON
        import json
        data = json.loads((tmp_path / "jobs.json").read_text())
        assert data["schema_version"] == 1
        assert len(data["jobs"]) == 1

    def test_corrupt_file_recovery(self, tmp_path):
        store_path = tmp_path / "jobs.json"
        store_path.write_text("not valid json {{{")

        store = ScheduledResearchStore(store_path)
        jobs = store.load()
        assert jobs == []
        # Corrupt file should be renamed
        assert not store_path.exists()


# ── Executor Tests ──────────────────────────────────────────


class TestScheduledResearchExecutor:
    def test_dispatch_fn_called(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        mock_dispatch = MagicMock()

        executor = ScheduledResearchExecutor(store, dispatch_fn=mock_dispatch)
        job = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        store.add(job)

        executor._dispatch(store.get(job.id))

        mock_dispatch.assert_called_once()

    def test_dispatch_marks_running_then_completed(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        job = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        store.add(job)

        executor = ScheduledResearchExecutor(store, dispatch_fn=lambda j: None)
        executor._dispatch(store.get(job.id))

        final = store.get(job.id)
        assert final.status == JobStatus.COMPLETED

    def test_dispatch_marks_failed_on_error(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        job = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        store.add(job)

        def failing_dispatch(j):
            raise RuntimeError("test error")

        executor = ScheduledResearchExecutor(store, dispatch_fn=failing_dispatch)
        executor._dispatch(store.get(job.id))

        final = store.get(job.id)
        assert final.status == JobStatus.FAILED

    def test_dispatch_updates_next_run_at(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        job = ScheduledResearchJob(
            workspace="/ws",
            strategy_name="s1",
            cron="0 * * * *",  # every hour
        )
        store.add(job)

        executor = ScheduledResearchExecutor(store, dispatch_fn=lambda j: None)
        executor._dispatch(store.get(job.id))

        final = store.get(job.id)
        assert final.next_run_at > job.next_run_at

    def test_run_once(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        mock_dispatch = MagicMock()
        job = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        store.add(job)

        executor = ScheduledResearchExecutor(store, dispatch_fn=mock_dispatch)
        ok = executor.run_once(job.id)

        assert ok
        mock_dispatch.assert_called_once()

    def test_run_once_missing(self, tmp_path):
        store = ScheduledResearchStore(tmp_path / "jobs.json")
        executor = ScheduledResearchExecutor(store)
        ok = executor.run_once("nonexistent")
        assert not ok


# ── CLI Tests ───────────────────────────────────────────────


class TestScheduleCLI:
    def test_schedule_help(self):
        from strategy_research.cli import main
        with pytest.raises(SystemExit) as exc_info:
            import sys
            sys.argv = ["quantnodes-research", "schedule", "--help"]
            main()
        assert exc_info.value.code == 0

    def test_schedule_create_help(self):
        from strategy_research.cli import main
        with pytest.raises(SystemExit) as exc_info:
            import sys
            sys.argv = ["quantnodes-research", "schedule", "create", "--help"]
            main()
        assert exc_info.value.code == 0

    def test_schedule_list_empty(self):
        from strategy_research.core.scheduled_research.cli import cmd_schedule_list
        import argparse
        args = argparse.Namespace(workspace=None)
        result = cmd_schedule_list(args)
        assert result == 0

    def test_schedule_create_and_list(self, tmp_path):
        from strategy_research.core.scheduled_research.cli import cmd_schedule_create, cmd_schedule_list
        import argparse

        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "config.yaml").write_text("workspace:\n  default_strategy: test\n")

        args = argparse.Namespace(
            workspace=str(ws),
            strategy="test",
            cron="0 2 * * *",
            interval=None,
            prompt="test prompt",
            max_rounds=1,
        )
        result = cmd_schedule_create(args)
        assert result == 0

        list_args = argparse.Namespace(workspace=None)
        result = cmd_schedule_list(list_args)
        assert result == 0
