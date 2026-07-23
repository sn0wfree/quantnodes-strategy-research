"""Scheduled Research store — JSON-backed persistence."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import List

from .models import JobStatus, ScheduledResearchJob

logger = logging.getLogger(__name__)

DEFAULT_STORE_PATH = Path.home() / ".quantnodes-research" / "scheduled_jobs.json"
SCHEMA_VERSION = 1


class ScheduledResearchStore:
    """JSON-backed store for scheduled research jobs.

    Storage: ~/.quantnodes-research/scheduled_jobs.json
    Atomic write: temp → fsync → replace
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else DEFAULT_STORE_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> List[ScheduledResearchJob]:
        """Load all jobs from disk."""
        if not self._path.exists():
            return []

        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Corrupt file — rename and start fresh
            corrupt_path = self._path.with_suffix(
                f".corrupt-{int(time.time())}"
            )
            self._path.rename(corrupt_path)
            logger.warning("Corrupt store renamed to %s", corrupt_path)
            return []

        if not isinstance(data, dict):
            return []

        jobs_data = data.get("jobs", [])
        return [ScheduledResearchJob.from_dict(j) for j in jobs_data]

    def save(self, jobs: List[ScheduledResearchJob]) -> None:
        """Save all jobs to disk (atomic write)."""
        data = {
            "schema_version": SCHEMA_VERSION,
            "jobs": [j.to_dict() for j in jobs],
        }
        payload = json.dumps(data, ensure_ascii=False, indent=2)

        # Atomic write: temp → fsync → replace
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=".scheduled_jobs-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(self._path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def add(self, job: ScheduledResearchJob) -> None:
        """Add a single job."""
        jobs = self.load()
        jobs.append(job)
        self.save(jobs)

    def get(self, job_id: str) -> ScheduledResearchJob | None:
        """Get a job by ID."""
        for job in self.load():
            if job.id == job_id:
                return job
        return None

    def update(self, job: ScheduledResearchJob) -> None:
        """Update a job (by ID)."""
        jobs = self.load()
        for i, j in enumerate(jobs):
            if j.id == job.id:
                jobs[i] = job
                self.save(jobs)
                return
        raise KeyError(f"Job not found: {job.id}")

    def delete(self, job_id: str) -> bool:
        """Delete a job by ID. Returns True if deleted."""
        jobs = self.load()
        original_len = len(jobs)
        jobs = [j for j in jobs if j.id != job_id]
        if len(jobs) < original_len:
            self.save(jobs)
            return True
        return False

    def list_jobs(
        self,
        workspace: str | None = None,
        status: JobStatus | None = None,
    ) -> List[ScheduledResearchJob]:
        """List jobs with optional filters."""
        jobs = self.load()
        if workspace:
            jobs = [j for j in jobs if j.workspace == workspace]
        if status:
            jobs = [j for j in jobs if j.status == status]
        return jobs

    def recover_stale_running(self) -> int:
        """Reset any RUNNING jobs back to PENDING (for crash recovery).

        Returns number of jobs recovered.
        """
        jobs = self.load()
        count = 0
        for job in jobs:
            if job.status == JobStatus.RUNNING:
                job.status = JobStatus.PENDING
                count += 1
        if count > 0:
            self.save(jobs)
        return count
