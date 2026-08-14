"""Tests for scheduled_research models and store."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.scheduled_research.models import JobStatus, ScheduledResearchJob
from strategy_research.core.scheduled_research.store import ScheduledResearchStore


class TestJobStatus(unittest.TestCase):

    def test_values(self) -> None:
        self.assertEqual(JobStatus.PENDING.value, "pending")
        self.assertEqual(JobStatus.RUNNING.value, "running")
        self.assertEqual(JobStatus.COMPLETED.value, "completed")
        self.assertEqual(JobStatus.FAILED.value, "failed")
        self.assertEqual(JobStatus.CANCELLED.value, "cancelled")

    def test_enum_membership(self) -> None:
        self.assertIn(JobStatus.PENDING, JobStatus)


class TestScheduledResearchJob(unittest.TestCase):

    def test_default_id_generated(self) -> None:
        job = ScheduledResearchJob()
        self.assertTrue(job.id.startswith("job_"))

    def test_default_created_at(self) -> None:
        job = ScheduledResearchJob()
        self.assertGreater(job.created_at, 0)

    def test_to_dict(self) -> None:
        job = ScheduledResearchJob(
            id="job_001",
            workspace="/ws",
            strategy_name="test_strat",
            cron="0 2 * * *",
            status=JobStatus.PENDING,
        )
        d = job.to_dict()
        self.assertEqual(d["id"], "job_001")
        self.assertEqual(d["status"], "pending")
        self.assertEqual(d["cron"], "0 2 * * *")

    def test_from_dict(self) -> None:
        data = {
            "id": "job_002",
            "workspace": "/ws",
            "strategy_name": "s2",
            "cron": "*/30 * * * *",
            "status": "running",
            "config": {"key": "val"},
        }
        job = ScheduledResearchJob.from_dict(data)
        self.assertEqual(job.id, "job_002")
        self.assertEqual(job.status, JobStatus.RUNNING)
        self.assertEqual(job.config["key"], "val")

    def test_from_dict_missing_fields(self) -> None:
        job = ScheduledResearchJob.from_dict({})
        self.assertIsInstance(job.id, str)
        self.assertEqual(job.status, JobStatus.PENDING)

    def test_is_due_pending(self) -> None:
        job = ScheduledResearchJob(next_run_at=100.0, status=JobStatus.PENDING)
        self.assertTrue(job.is_due(now=200.0))

    def test_is_due_not_due(self) -> None:
        job = ScheduledResearchJob(next_run_at=300.0, status=JobStatus.PENDING)
        self.assertFalse(job.is_due(now=200.0))

    def test_is_due_running_not_due(self) -> None:
        job = ScheduledResearchJob(next_run_at=100.0, status=JobStatus.RUNNING)
        self.assertFalse(job.is_due(now=200.0))

    def test_is_due_completed(self) -> None:
        job = ScheduledResearchJob(
            next_run_at=100.0, status=JobStatus.COMPLETED
        )
        self.assertTrue(job.is_due(now=200.0))

    def test_is_due_failed(self) -> None:
        job = ScheduledResearchJob(next_run_at=100.0, status=JobStatus.FAILED)
        self.assertFalse(job.is_due(now=200.0))

    def test_is_due_cancelled(self) -> None:
        job = ScheduledResearchJob(next_run_at=100.0, status=JobStatus.CANCELLED)
        self.assertFalse(job.is_due(now=200.0))

    def test_is_due_no_next_run(self) -> None:
        job = ScheduledResearchJob(next_run_at=0.0, status=JobStatus.PENDING)
        self.assertFalse(job.is_due(now=200.0))

    def test_is_recurring_cron(self) -> None:
        job = ScheduledResearchJob(cron="0 * * * *")
        self.assertTrue(job.is_recurring())

    def test_is_recurring_interval(self) -> None:
        job = ScheduledResearchJob(interval_ms=3600000)
        self.assertTrue(job.is_recurring())

    def test_is_recurring_one_shot(self) -> None:
        job = ScheduledResearchJob()
        self.assertFalse(job.is_recurring())


class TestScheduledResearchStore(unittest.TestCase):

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.store_path = Path(self.tmpdir.name) / "jobs.json"
        self.store = ScheduledResearchStore(path=self.store_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_load_empty(self) -> None:
        self.assertEqual(self.store.load(), [])

    def test_add_and_load(self) -> None:
        job = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        self.store.add(job)
        jobs = self.store.load()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].workspace, "/ws")

    def test_get_found(self) -> None:
        job = ScheduledResearchJob(id="find_me", workspace="/ws")
        self.store.add(job)
        found = self.store.get("find_me")
        self.assertIsNotNone(found)
        self.assertEqual(found.workspace, "/ws")

    def test_get_not_found(self) -> None:
        self.assertIsNone(self.store.get("nonexistent"))

    def test_update(self) -> None:
        job = ScheduledResearchJob(id="upd", workspace="/ws")
        self.store.add(job)
        job.workspace = "/new_ws"
        self.store.update(job)
        found = self.store.get("upd")
        self.assertEqual(found.workspace, "/new_ws")

    def test_update_not_found_raises(self) -> None:
        job = ScheduledResearchJob(id="ghost", workspace="/ws")
        with self.assertRaises(KeyError):
            self.store.update(job)

    def test_delete(self) -> None:
        job = ScheduledResearchJob(id="del_me", workspace="/ws")
        self.store.add(job)
        self.assertTrue(self.store.delete("del_me"))
        self.assertIsNone(self.store.get("del_me"))

    def test_delete_not_found(self) -> None:
        self.assertFalse(self.store.delete("nonexistent"))

    def test_list_jobs_no_filter(self) -> None:
        self.store.add(ScheduledResearchJob(workspace="/ws1"))
        self.store.add(ScheduledResearchJob(workspace="/ws2"))
        self.assertEqual(len(self.store.list_jobs()), 2)

    def test_list_jobs_filter_workspace(self) -> None:
        self.store.add(ScheduledResearchJob(workspace="/ws1"))
        self.store.add(ScheduledResearchJob(workspace="/ws2"))
        filtered = self.store.list_jobs(workspace="/ws1")
        self.assertEqual(len(filtered), 1)

    def test_list_jobs_filter_status(self) -> None:
        j1 = ScheduledResearchJob(workspace="/ws", status=JobStatus.PENDING)
        j2 = ScheduledResearchJob(workspace="/ws", status=JobStatus.COMPLETED)
        self.store.add(j1)
        self.store.add(j2)
        filtered = self.store.list_jobs(status=JobStatus.PENDING)
        self.assertEqual(len(filtered), 1)

    def test_recover_stale_running(self) -> None:
        job = ScheduledResearchJob(workspace="/ws", status=JobStatus.RUNNING)
        self.store.add(job)
        count = self.store.recover_stale_running()
        self.assertEqual(count, 1)
        recovered = self.store.get(job.id)
        self.assertEqual(recovered.status, JobStatus.PENDING)

    def test_recover_no_stale(self) -> None:
        job = ScheduledResearchJob(workspace="/ws", status=JobStatus.PENDING)
        self.store.add(job)
        count = self.store.recover_stale_running()
        self.assertEqual(count, 0)

    def test_corrupt_file_renamed(self) -> None:
        self.store_path.write_text("not json", encoding="utf-8")
        jobs = self.store.load()
        self.assertEqual(jobs, [])
        self.assertFalse(self.store_path.exists())
        self.assertTrue(
            any(".corrupt-" in p.name
                for p in self.store_path.parent.iterdir())
        )

    def test_save_atomicity(self) -> None:
        job = ScheduledResearchJob(workspace="/ws")
        self.store.add(job)
        self.assertTrue(self.store_path.exists())
        data = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(len(data["jobs"]), 1)


if __name__ == "__main__":
    unittest.main()
