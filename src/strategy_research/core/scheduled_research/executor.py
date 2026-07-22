"""Scheduled Research executor — asyncio-based scheduler daemon."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import Callable

from .cron_parser import next_cron_trigger
from .models import JobStatus, ScheduledResearchJob
from .store import ScheduledResearchStore

logger = logging.getLogger(__name__)


class ScheduledResearchExecutor:
    """Async scheduler that runs research jobs at specified times.

    Usage:
        store = ScheduledResearchStore()
        executor = ScheduledResearchExecutor(store)
        executor.start()  # starts in background thread
        # ... later ...
        executor.stop()
    """

    def __init__(
        self,
        store: ScheduledResearchStore,
        tick_interval: float = 60.0,
        dispatch_fn: Callable[[ScheduledResearchJob], None] | None = None,
    ) -> None:
        self._store = store
        self._tick_interval = tick_interval
        self._dispatch_fn = dispatch_fn or self._default_dispatch
        self._running = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        """Start the scheduler in a background thread."""
        if self._running:
            return

        self._running = True
        self._loop = asyncio.new_event_loop()
        self._task = asyncio.ensure_future(self._run_loop(), loop=self._loop)

        import threading
        self._thread = threading.Thread(
            target=self._run_loop_sync,
            daemon=True,
            name="scheduled-research-executor",
        )
        self._thread.start()
        logger.info("Scheduled research executor started (tick=%ss)", self._tick_interval)

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task and self._loop:
            self._loop.call_soon_threadsafe(self._task.cancel)
        logger.info("Scheduled research executor stopped")

    def run_once(self, job_id: str) -> bool:
        """Immediately run a specific job once.

        Returns True if the job was found and dispatched.
        """
        job = self._store.get(job_id)
        if job is None:
            return False
        self._dispatch(job)
        return True

    def _run_loop_sync(self) -> None:
        """Run the event loop in a thread."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._run_loop())

    async def _run_loop(self) -> None:
        """Main scheduler loop."""
        # Recover stale running jobs
        recovered = self._store.recover_stale_running()
        if recovered:
            logger.info("Recovered %d stale running jobs", recovered)

        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(self._tick_interval)

    async def _tick(self) -> None:
        """Check for due jobs and dispatch them."""
        now = time.time()
        jobs = self._store.load()

        for job in jobs:
            if job.is_due(now):
                # Run dispatch in executor to avoid blocking the tick
                await asyncio.get_event_loop().run_in_executor(
                    None, self._dispatch, job
                )

    def _dispatch(self, job: ScheduledResearchJob) -> None:
        """Dispatch a single job (runs in thread executor)."""
        logger.info("Dispatching job %s (%s/%s)", job.id, job.workspace, job.strategy_name)

        # Mark as running
        job.status = JobStatus.RUNNING
        job.last_run_at = time.time()
        self._store.update(job)

        try:
            self._dispatch_fn(job)
            # Success
            job.status = JobStatus.COMPLETED
            logger.info("Job %s completed successfully", job.id)
        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.FAILED
            job.config["last_error"] = str(exc)
            logger.error("Job %s failed: %s", job.id, exc)

        # Update next_run_at for recurring jobs
        if job.is_recurring():
            try:
                if job.cron:
                    job.next_run_at = next_cron_trigger(job.cron)
                elif job.interval_ms > 0:
                    job.next_run_at = time.time() + job.interval_ms / 1000
                job.status = JobStatus.PENDING  # Reset for next run
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to compute next_run_at for %s: %s", job.id, exc)
                job.status = JobStatus.FAILED

        self._store.update(job)

    def _default_dispatch(self, job: ScheduledResearchJob) -> None:
        """Default dispatch: run autoresearch as subprocess."""
        cmd = [
            "quantnodes-research", "autoresearch",
            job.workspace,
            "--strategy", job.strategy_name,
            "--max-rounds", str(job.max_rounds),
        ]
        if job.prompt:
            cmd.extend(["--prompt", job.prompt])

        logger.info("Running: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour max
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"autoresearch failed (exit {result.returncode}): {result.stderr[:500]}"
            )

        logger.info("autoresearch completed for job %s", job.id)
