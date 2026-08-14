"""StudyScheduler — per-session serial execution of studies.

Phase 1 scope (see docs/study-longhorizon-plan.md):
    - submit a study → enqueue on its session's queue
    - per-session consumer loop → run one study at a time via
      AutoresearchExecutor
    - cooperative mutex with chat attempts: ask SessionService whether
      the session is currently processing before launching the executor
      and claim the processing slot for the study's lifetime
    - pause / resume / cancel via per-study ControlToken
    - recover_on_startup: re-enqueue studies left running/queued from a
      previous process
    - emit study_* events on the session's event_bus so the WebUI SSE
      channel already wired for chat also carries study progress
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from .models import StudyRecord, StudyStatus
from .runner import AutoresearchRunner, ControlToken, NullEmitter
from .store import StudyStore

logger = logging.getLogger(__name__)

# v2: global concurrency cap across ALL studies (per-process semaphore).
# Study directories are autonomous (study/<id>/), so no per-strategy locks.
SR_STUDY_MAX_CONCURRENT = int(os.environ.get("SR_STUDY_MAX_CONCURRENT", "3"))

# Watchdog: a RUNNING study whose heartbeat is older than this is force-
# interrupted (heartbeat is bumped once per round; real-LLM rounds can run
# long, hence the conservative default of 1h).
SR_STUDY_HEARTBEAT_TIMEOUT = int(os.environ.get("SR_STUDY_HEARTBEAT_TIMEOUT", "3600"))
SR_STUDY_WATCHDOG_INTERVAL = int(os.environ.get("SR_STUDY_WATCHDOG_INTERVAL", "60"))

# Statuses that must never be (re-)executed: submitting one is rejected.
# MONITORING / INTERRUPTED / PAUSED remain resumable (recover path).
_TERMINAL_STATUSES = frozenset({
    StudyStatus.COMPLETE,
    StudyStatus.CANCELLED,
    StudyStatus.ERROR,
    StudyStatus.BUDGET_LIMITED,
    StudyStatus.EARLY_STOPPED,
    StudyStatus.NEEDS_REFRESH,
})


def _dlog(module: str, msg: str, *args) -> None:
    """Dual-output log: logger + stderr so both file and terminal see it."""
    msg_fmt = msg % args if args else msg
    logger.info("[STUDY:%s] %s", module, msg_fmt)
    print(f"[STUDY:{module}] {msg_fmt}", flush=True)  # noqa: T201


class StudyScheduler:
    """Per-session serial scheduler for study executors.

    Holds one asyncio task per study, plus one consumer per session that
    drains the session's study queue. Markup on SessionService:
    ``is_session_processing`` is consulted before launching an executor;
    when None (chat idle) the scheduler claims the slot via
    ``mark_session_processing`` so concurrent chat attempts block.
    """

    def __init__(
        self,
        store: StudyStore,
        *,
        session_service: Any | None = None,
        emitter_factory: Any | None = None,
    ) -> None:
        self.store = store
        self.session_service = session_service
        # emitter_factory(session_id) -> an object with .emit(session_id, event, data)
        # If None we use a NullEmitter per study.
        self._emitter_factory = emitter_factory
        # Active state
        self._active_executors: dict[str, AutoresearchRunner] = {}
        self._control_tokens: dict[str, ControlToken] = {}
        self._active_tasks: dict[str, asyncio.Task] = {}
        # Per-session queue + consumer
        self._session_queues: dict[str, asyncio.Queue] = {}
        self._session_consumers: dict[str, asyncio.Task] = {}
        # v2: fire-and-forget dispatch tasks (session_loop → _run_one_study)
        self._dispatch_tasks: dict[str, asyncio.Task] = {}
        # v2: global concurrency semaphore (SR_STUDY_MAX_CONCURRENT)
        self._semaphore = asyncio.Semaphore(max(1, SR_STUDY_MAX_CONCURRENT))
        # Dedupe guard: study_ids sitting in a session queue (submitted but
        # not yet picked up). Prevents double-enqueue of the same study.
        self._queued_study_ids: set[str] = set()
        # Watchdog: done-task cleanup + heartbeat-stale interruption.
        self._watchdog_task: asyncio.Task | None = None
        self._watchdog_interval = max(10, SR_STUDY_WATCHDOG_INTERVAL)
        self._heartbeat_timeout = max(60, SR_STUDY_HEARTBEAT_TIMEOUT)
        self._shutdown = False

    # ── public: submit ─────────────────────────────────────────────

    async def submit(self, study: StudyRecord) -> bool:
        """Enqueue a study for execution on its session.

        Returns ``False`` (and does nothing) when the study is already
        queued / active / held by the scheduler or has reached a terminal
        status — a duplicate submit must never run a study twice.
        """
        sid = study.study_id
        # 1. Already active (running / dispatching / paused via token)?
        if sid in self._active_executors or sid in self._active_tasks \
                or sid in self._dispatch_tasks or sid in self._control_tokens:
            _dlog("sched", "submit rejected: study already active %s", sid)
            return False
        # 2. Already sitting in its session queue?
        if sid in self._queued_study_ids:
            _dlog("sched", "submit rejected: study already queued %s", sid)
            return False
        # 3. Store is authoritative for lifecycle: a terminal study must
        #    never be re-executed (e.g. duplicate start of a finished one).
        current = self.store.get_study(sid)
        if current is not None and current.execution_status in _TERMINAL_STATUSES:
            _dlog("sched", "submit rejected: study terminal %s (%s)",
                  sid, current.execution_status.value)
            return False

        _dlog("sched", "submit study=%s session=%s status=%s",
              sid, study.session_id, study.execution_status.value)
        # Mark queued in store (defensive; create_study already sets it,
        # but if submit() is called on a recovered RUNNING study, we do
        # not downgrade it here — only fresh submits pass through).
        # INTERRUPTED studies are reset to QUEUED so the runner picks them up.
        # MONITORING studies stay MONITORING (v2 §15.2 recover → monitor task).
        if study.execution_status not in (
            StudyStatus.RUNNING, StudyStatus.INTERRUPTED, StudyStatus.MONITORING,
        ):
            self.store.update_execution_status(
                study.study_id, StudyStatus.QUEUED,
            )
        elif study.execution_status == StudyStatus.INTERRUPTED:
            self.store.update_execution_status(
                study.study_id, StudyStatus.QUEUED,
                last_error=None,
            )
        self._emit_event(study.session_id, "study_queued", {
            "study_id": study.study_id, "session_id": study.session_id,
            "objective": study.objective,
        })

        # Ensure per-session queue + consumer are alive
        q = self._session_queues.setdefault(study.session_id, asyncio.Queue())
        self._queued_study_ids.add(sid)
        await q.put(study.study_id)
        self._ensure_consumer(study.session_id)
        self._ensure_watchdog()
        return True

    # ── public: control ────────────────────────────────────────────

    def pause(self, study_id: str) -> bool:
        tok = self._control_tokens.get(study_id)
        if tok is None:
            return False
        tok.paused = True
        return True

    def resume(self, study_id: str) -> bool:
        tok = self._control_tokens.get(study_id)
        if tok is None:
            return False
        tok.paused = False
        return True

    async def resume_interrupted(self, study_id: str) -> bool:
        """Resume an INTERRUPTED study by re-submitting it to the scheduler.

        Unlike ``resume()`` which unpauses an active runner, this method
        rebuilds the runner from the persisted study state and queues it
        on the session's consumer loop.
        """
        study = self.store.get_study(study_id)
        if study is None:
            return False
        if study.execution_status != StudyStatus.INTERRUPTED:
            return False
        await self.submit(study)
        return True

    def cancel(self, study_id: str) -> bool:
        tok = self._control_tokens.get(study_id)
        if tok is None:
            return False
        tok.cancelled = True
        return True

    # ── public: introspection ─────────────────────────────────────

    def is_running(self, study_id: str) -> bool:
        return study_id in self._active_executors

    def active_studies(self) -> list[str]:
        return list(self._active_executors.keys())

    # ── startup recovery ──────────────────────────────────────────

    async def recover_on_startup(self) -> list[StudyRecord]:
        """Recover studies from a previous run.

        Policy:
        - RUNNING → INTERRUPTED (manual resume required; prevents auto-restart
          loops when uvicorn reload kills the study due to file changes)
        - PAUSED → stays PAUSED (user pause is respected)
        - QUEUED → stays QUEUED but is re-submitted to the scheduler
        """
        # Memory guard: process-local state is empty at startup so there
        # is no false-positive "ghost" to filter (unlike chat attempts we
        # need no inter-process coordination).
        recoverable: list[StudyRecord] = []
        for s in self.store.list_active_studies():
            if s.execution_status == StudyStatus.PAUSED:
                continue  # respect user pause
            if s.execution_status == StudyStatus.RUNNING:
                # Running study was killed by restart; mark as INTERRUPTED
                # so user can manually resume. Do NOT auto-resubmit.
                self.store.update_execution_status(
                    s.study_id, StudyStatus.INTERRUPTED,
                    last_error="interrupted by server restart",
                )
                self._emit_event(s.session_id, "study_interrupted", {
                    "study_id": s.study_id,
                    "session_id": s.session_id,
                    "round": s.current_round,
                    "reason": "interrupted by server restart",
                })
                recoverable.append(s)
                continue
            if s.execution_status == StudyStatus.MONITORING:
                # v2 §15.2 recover: rebuild the monitor task, no research rounds.
                recoverable.append(s)
                await self.submit(s)
                continue
            # QUEUED: re-enqueue so the consumer picks it up
            recoverable.append(s)
            await self.submit(s)
        if recoverable:
            logger.info(
                "study scheduler recovered %d studies", len(recoverable),
            )
        return recoverable

    # ── public: shutdown ───────────────────────────────────────────

    async def shutdown(self) -> None:
        """Cancel all active executors + consumers (best-effort)."""
        self._shutdown = True
        for sid, tok in list(self._control_tokens.items()):
            tok.cancelled = True
        for _sid, task in list(self._active_tasks.items()):
            task.cancel()
        for _sid, task in list(self._dispatch_tasks.items()):
            task.cancel()
        for _sid, task in list(self._session_consumers.items()):
            task.cancel()
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        # give tasks a moment to unwind
        await asyncio.sleep(0)
        self._active_tasks.clear()
        self._dispatch_tasks.clear()
        self._session_consumers.clear()
        self._queued_study_ids.clear()

    # ── internals ──────────────────────────────────────────────────

    def _ensure_consumer(self, session_id: str) -> None:
        if session_id in self._session_consumers:
            return
        task = asyncio.create_task(self._session_loop(session_id))
        self._session_consumers[session_id] = task
        task.add_done_callback(
            lambda t: self._session_consumers.pop(session_id, None)
        )

    async def _session_loop(self, session_id: str) -> None:
        """Drain the session's study queue, dispatching studies in parallel."""
        q = self._session_queues.get(session_id)
        logger.info("study session_loop start session=%s", session_id)
        while not self._shutdown and q is not None:
            try:
                study_id = await q.get()
            except asyncio.CancelledError:
                break
            if study_id is None:
                break
            logger.info("study session_loop got study=%s", study_id)
            self._queued_study_ids.discard(study_id)
            # v2: true parallelism — do NOT await the study to completion;
            # the global semaphore inside _run_one_study caps concurrency.
            task = asyncio.create_task(self._run_one_study(study_id))
            self._dispatch_tasks[study_id] = task
            task.add_done_callback(
                lambda t, sid=study_id: self._dispatch_tasks.pop(sid, None)
            )
        # queue drained — drop it (next submit recreates)
        self._session_queues.pop(session_id, None)
        logger.info("study session_loop exit session=%s", session_id)

    async def _run_one_study(self, study_id: str) -> None:
        _dlog("sched", "_run_one_study start study=%s", study_id)
        # Global concurrency cap: holds the slot for the whole study
        # lifetime (rounds + cooldown + review), per design §5.2.
        await self._semaphore.acquire()
        try:
            await self._run_one_study_locked(study_id)
        finally:
            self._semaphore.release()

    async def _run_one_study_locked(self, study_id: str) -> None:
        _dlog("sched", "_run_one_study_locked study=%s", study_id)
        study = self.store.get_study(study_id)
        if study is None:
            _dlog("sched", "_run_one_study: study not found %s", study_id)
            return
        # Defense in depth: the store is authoritative. A study that went
        # terminal between enqueue and dispatch (e.g. cancelled elsewhere)
        # must not execute — a stale queue entry is silently dropped.
        if study.execution_status in _TERMINAL_STATUSES:
            _dlog("sched", "_run_one_study: drop terminal %s (%s)",
                  study_id, study.execution_status.value)
            return
        # Cooperative mutex with chat: if chat is mid-loop, wait for it.
        if self.session_service is not None:
            logger.info(
                "study waiting-for-chat session=%s processing=%s",
                study.session_id,
                self.session_service.is_session_processing(study.session_id),
            )
        while self.session_service is not None and \
                self.session_service.is_session_processing(study.session_id):
            await asyncio.sleep(0.25)
            # recheck in case the chat queue is paused — continue waiting
        # Claim the slot for the study.
        if self.session_service is not None:
            self.session_service.mark_session_processing(
                study.session_id, processing=True,
            )

        control = ControlToken()
        self._control_tokens[study_id] = control
        emitter = self._make_emitter(study)
        executor = AutoresearchRunner(
            study, self.store, control=control, emitter=emitter,
        )
        self._active_executors[study_id] = executor
        _dlog("sched", "marking RUNNING study=%s", study_id)
        # v2 §15.2: a recovered MONITORING study keeps its status; the runner
        # skips research rounds and enters the monitor phase directly.
        if study.execution_status != StudyStatus.MONITORING:
            self.store.update_execution_status(
                study_id, StudyStatus.RUNNING,
            )
        self._emit_event(study.session_id, "study_started", {
            "study_id": study_id, "round": study.current_round,
        })

        task = asyncio.create_task(executor.run())
        self._active_tasks[study_id] = task
        _dlog("sched", "executor task created study=%s", study_id)
        try:
            await task
            _dlog("sched", "executor task finished study=%s", study_id)
        except asyncio.CancelledError:
            control.cancelled = True
        finally:
            self._active_executors.pop(study_id, None)
            self._control_tokens.pop(study_id, None)
            self._active_tasks.pop(study_id, None)
            if self.session_service is not None:
                self.session_service.mark_session_processing(
                    study.session_id, processing=False,
                )

    # ── watchdog: task health + heartbeat staleness ────────────────

    def _ensure_watchdog(self) -> None:
        """Lazily start the background watchdog (one per scheduler)."""
        if self._watchdog_task is not None:
            return
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        try:
            while not self._shutdown:
                await asyncio.sleep(self._watchdog_interval)
                await self._watchdog_tick()
        except asyncio.CancelledError:
            pass

    async def _watchdog_tick(self) -> None:
        """Sweep active tasks: cleanup dead ones, interrupt stale ones."""
        # 1. Tasks that finished without the normal cleanup path → mark
        #    INTERRUPTED so the row is not left RUNNING forever.
        for sid in list(self._active_tasks):
            task = self._active_tasks.get(sid)
            if task is None or not task.done():
                continue
            self._active_tasks.pop(sid, None)
            self._dispatch_tasks.pop(sid, None)
            self._active_executors.pop(sid, None)
            tok = self._control_tokens.pop(sid, None)
            record = self.store.get_study(sid)
            if record is not None and \
                    record.execution_status not in _TERMINAL_STATUSES:
                self.store.update_execution_status(
                    sid, StudyStatus.INTERRUPTED,
                    last_error="watchdog: task ended without cleanup",
                )
                self._emit_event(record.session_id, "study_interrupted", {
                    "study_id": sid,
                    "session_id": record.session_id,
                    "round": record.current_round,
                    "reason": "watchdog: task ended without cleanup",
                })
            elif tok is not None:
                # Terminal study with a lingering token — just drop it.
                self._control_tokens.pop(sid, None)

        # 2. Heartbeat stale → force-cancel the executor task + INTERRUPTED.
        now = datetime.now(timezone.utc)
        for sid, task in list(self._active_tasks.items()):
            record = self.store.get_study(sid)
            if record is None or not record.heartbeat:
                continue
            try:
                hb = datetime.fromisoformat(record.heartbeat)
            except ValueError:
                continue
            age = (now - hb).total_seconds()
            if age <= self._heartbeat_timeout:
                continue
            _dlog("sched", "watchdog: heartbeat stale study=%s age=%.0fs",
                  sid, age)
            tok = self._control_tokens.get(sid)
            if tok is not None:
                tok.cancelled = True
            task.cancel()
            self.store.update_execution_status(
                sid, StudyStatus.INTERRUPTED,
                last_error=f"watchdog: heartbeat stale ({age:.0f}s)",
            )
            self._emit_event(record.session_id, "study_interrupted", {
                "study_id": sid,
                "session_id": record.session_id,
                "round": record.current_round,
                "reason": "heartbeat stale",
            })

        # 3. Background-task registry: any live task whose log stopped
        #    advancing is stuck → kill + deregister (log-progress liveness).
        from ..utils.bg_proc import (
            active_tasks,
            is_stalled,
            kill_bg,
            log_tail,
            unregister_task,
        )
        for handle in active_tasks():
            if not is_stalled(handle.log_path, self._heartbeat_timeout):
                continue
            _dlog("sched", "watchdog: bg task stalled task=%s log=%s",
                  handle.task_id, handle.log_path)
            if handle.proc is not None:
                kill_bg(handle.proc)
            unregister_task(handle.task_id)
            self._emit_event(
                handle.owner or "system", "study_interrupted", {
                    "study_id": handle.owner or "",
                    "session_id": handle.owner or "",
                    "reason": f"bg task stalled ({handle.task_id})",
                    "tail": log_tail(handle.log_path, n=3),
                },
            )

    def _make_emitter(self, study: StudyRecord):
        """Construct an emitter bound to the study's session event_bus.

        Resolution order:
          1. explicit ``emitter_factory`` (tests / custom sinks)
          2. the session's EventBus (real service) — forwards every
             study_* event from the executor into the SSE stream so the
             WebUI Study panel updates instantly
          3. NullEmitter (no service wiring — e.g. unit tests)
        """
        if self._emitter_factory is not None:
            return self._emitter_factory(study.session_id)
        if self.session_service is not None and \
                self.session_service.event_bus is not None:
            return make_event_bus_emitter(study.session_id,
                                          self.session_service.event_bus)
        return NullEmitter()

    def _emit_event(self, session_id: str, event: str, data: dict) -> None:
        if self.session_service is None:
            return
        try:
            self.session_service.event_bus.emit(session_id, event, data)
        except Exception as exc:
            logger.debug("study scheduler emit %s failed: %s", event, exc)


# ── emitter factory: bridge EventBusV2 ──────────────────────────────


def make_event_bus_emitter(session_id: str, event_bus: Any) -> "_EventBusEmitter":
    """Factory helper: bind EventBusV2 emission to a session id."""
    return _EventBusEmitter(session_id, event_bus)


class _EventBusEmitter:
    """Wraps EventBusV2.emit() so it matches the EventEmitter protocol."""

    def __init__(self, session_id: str, event_bus: Any) -> None:
        self.session_id = session_id
        self.event_bus = event_bus

    def emit(self, session_id: str, event: str, data: dict) -> None:
        # Use the session the listener subscribed to, not the executor's
        # session: they match in practice for Phase 1.
        self.event_bus.emit(session_id, event, data)
