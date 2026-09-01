"""Archived dead code: v1 chat/study cooperative mutex (2026-09).

Originally lived in ``src/strategy_research/core/study/scheduler.py`` (lines
804-819 + 851-854) and was the v1 design's chat/study mutex implementation.

**Why archived (not deleted)**:
- The v2 identity refactor (``docs/study-longhorizon-v2-design.md`` §4.2)
  made ``study.session_id == study_id``, decoupling chat and study sessions.
- The ``while self.session_service.is_session_processing(study.session_id)``
  loop at the old L811-813 became a no-op: chat tracks chat session IDs,
  not study IDs, so the predicate is always False → 0 iterations.
- The matching ``mark_session_processing(..., True)`` at the old L815-819
  claimed the slot for the study itself (``study.session_id == study_id``),
  then released it in the finally block at the old L851-854.
- Net effect: a "dead-but-harmless no-op" that misleads maintainers into
  thinking chat and study are still mutually exclusive.

**Why not also remove the methods on SessionService**:
- ``is_session_processing`` and ``mark_session_processing`` are still
  actively used by ``src/strategy_research/core/goal/workflow.py`` (L463,
  L465, L478) for the goal workflow ↔ chat mutex.
- The methods themselves remain live; only the scheduler's callers were
  dead.

**Original code** (verbatim, for archaeological reference):

.. code-block:: python

    # ── Cooperative mutex with chat: if chat is mid-loop, wait for it.
    if self.session_service is not None:
        logger.info(
            "study waiting-for-chat session=%s processing=%s",
            study.session_id,
            self.session_service.is_session_processing(study.session_id),
        )
    while self.session_service is not None and \\
            self.session_service.is_session_processing(study.session_id):
        await asyncio.sleep(0.25)
        # recheck in case the chat queue is paused — continue waiting
    # Claim the slot for the study.
    if self.session_service is not None:
        self.session_service.mark_session_processing(
            study.session_id, processing=True,
        )

    # …later, in finally:
    if self.session_service is not None:
        self.session_service.mark_session_processing(
            study.session_id, processing=False,
        )
"""