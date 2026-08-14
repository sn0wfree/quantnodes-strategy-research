"""Background process utilities — nohup-style long tasks with log-driven
liveness detection.

Any long-running program (backtest, factor computation, data prep, training,
...) can be backgrounded via ``run_bg`` + ``wait_bg``; liveness is judged by
the *log file* making progress, not by wall clock:

- log mtime keeps advancing  → the task is alive and healthy (no timeout —
  run as long as it needs)
- process alive but log stalled > ``stall_timeout`` → the task is stuck →
  ``killpg`` the whole process group
- process exited → return its full log output for parsing

See ``docs/study-long-task-background-plan.md`` §2 for the judgement model.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_STALL_TIMEOUT = float(
    os.environ.get("SR_BACKTEST_STALL_TIMEOUT", "300")
)


def run_bg(
    command: Sequence[str],
    log_path: Path | str,
    *,
    env: Optional[dict[str, str]] = None,
    cwd: Path | str | None = None,
) -> subprocess.Popen:
    """Start ``command`` in the background, streaming stdout+stderr to
    ``log_path`` (nohup semantics).

    - ``start_new_session=True``: the child gets its own process group so
      ``killpg`` can reap grandchildren.
    - Returns immediately (non-blocking); call ``wait_bg`` to poll.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 — long-lived handle
    proc = subprocess.Popen(  # noqa: S603
        list(command),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=True,
    )
    return proc


def log_tail(log_path: Path | str, n: int = 20) -> str:
    """Return the last ``n`` lines of a log file (missing file → empty)."""
    path = Path(log_path)
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def is_stalled(log_path: Path | str, stall_timeout: float = DEFAULT_STALL_TIMEOUT,
               *, since: Optional[float] = None) -> bool:
    """True when the log stopped advancing ``stall_timeout`` seconds ago.

    ``since`` defaults to the current time; a missing log file counts as
    stalled (callers should guard with a startup grace window).
    """
    path = Path(log_path)
    if not path.exists():
        return True
    mtime = path.stat().st_mtime
    ref = since if since is not None else time.time()
    return (ref - mtime) > stall_timeout


def kill_bg(proc: subprocess.Popen) -> None:
    """Kill the whole process group (children included)."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # noqa: S606
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, Exception):  # noqa: BLE001
        pass


def wait_bg(
    proc: subprocess.Popen,
    log_path: Path | str,
    stall_timeout: float = DEFAULT_STALL_TIMEOUT,
    *,
    poll: float = 2.0,
) -> tuple[bool, str]:
    """Poll a background process until it exits or stalls.

    Returns ``(ok, payload)``:
    - ``(True, log_output)`` — process exited (any exit code); payload is
      the full log text.
    - ``(False, "stalled after Ns...")`` — process alive but the log
      stopped advancing; the process group has been killed.

    A startup grace window (``max(10s, 5×poll)``) allows the child to
    spawn and write its first log lines before staleness applies.
    """
    log_path = Path(log_path)
    grace_until = time.time() + max(10.0, poll * 5)
    while True:
        if proc.poll() is not None:
            break
        if time.time() > grace_until and is_stalled(log_path, stall_timeout):
            kill_bg(proc)
            return False, (
                f"stalled after {stall_timeout:.0f}s without log progress "
                f"(log: {log_path})"
            )
        time.sleep(poll)
    output = log_path.read_text(encoding="utf-8", errors="replace") \
        if log_path.exists() else ""
    return True, output


def log_progress(log_path: Path | str, line: str) -> None:
    """Append a progress line to a log (in-process/thread-mode writers)."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{line}\n")
