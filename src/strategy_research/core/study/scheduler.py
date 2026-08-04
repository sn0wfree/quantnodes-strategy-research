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
from typing import Any

from .runner import AutoresearchRunner, ControlToken, NullEmitter
from .models import StudyRecord, StudyStatus
from .store import StudyStore

logger = logging.getLogger(__name__)


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
        self._shutdown = False

    # ── public: submit ─────────────────────────────────────────────

    async def submit(self, study: StudyRecord) -> None:
        """Enqueue a study for execution on its session."""
        _dlog("sched", "submit study=%s session=%s status=%s",
              study.study_id, study.session_id, study.execution_status.value)
        # Mark queued in store (defensive; create_study already sets it,
        # but if submit() is called on a recovered RUNNING study, we do
        # not downgrade it here — only fresh submits pass through).
        if study.execution_status != StudyStatus.RUNNING:
            self.store.update_execution_status(
                study.study_id, StudyStatus.QUEUED,
            )
        self._emit_event(study.session_id, "study_queued", {
            "study_id": study.study_id, "session_id": study.session_id,
            "objective": study.objective,
        })

        # Ensure per-session queue + consumer are alive
        q = self._session_queues.setdefault(study.session_id, asyncio.Queue())
        await q.put(study.study_id)
        self._ensure_consumer(study.session_id)

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
        """Re-enqueue studies left queued/running from a previous run.

        Policy: RUNNING studies are reset to QUEUED and re-enqueued; the
        executor seeds its ``round_num`` from the study's persisted
        ``current_round`` so the autoresearch run numbering continues —
        workers resume by reading ``runs/`` on disk. PAUSED studies stay
        paused (the user can resume via /study resume).
        """
        # Memory guard: process-local state is empty at startup so there
        # is no false-positive "ghost" to filter (unlike chat attempts we
        # need no inter-process coordination).
        recoverable: list[StudyRecord] = []
        for s in self.store.list_active_studies():
            if s.execution_status == StudyStatus.PAUSED:
                continue  # respect user pause
            if s.execution_status == StudyStatus.RUNNING:
                # A running study was lost on restart; requeue.
                self.store.update_execution_status(
                    s.study_id, StudyStatus.QUEUED,
                    last_error="rescheduled after restart",
                )
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
        for _sid, task in list(self._session_consumers.items()):
            task.cancel()
        # give tasks a moment to unwind
        await asyncio.sleep(0)
        self._active_tasks.clear()
        self._session_consumers.clear()

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
        """Drain the session's study queue one at a time."""
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
            await self._run_one_study(study_id)
        # queue drained — drop it (next submit recreates)
        self._session_queues.pop(session_id, None)
        logger.info("study session_loop exit session=%s", session_id)

    async def _run_one_study(self, study_id: str) -> None:
        _dlog("sched", "_run_one_study start study=%s", study_id)
        study = self.store.get_study(study_id)
        if study is None:
            _dlog("sched", "_run_one_study: study not found %s", study_id)
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