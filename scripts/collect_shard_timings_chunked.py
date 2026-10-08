#!/usr/bin/env python
"""Build a `.shard-timings.json` by chunked pytest runs with per-test timeouts.

`scripts/collect_shard_timings.py` runs the full suite in one process — too
slow on Windows when a few tests are slow. This version:
  * Splits `tests/` into batches of N files at a time.
  * Each batch runs `pytest --durations=0 -q --timeout=30` so a hung test
    can't stall the whole collector.
  * Per-batch timings are merged into the output JSON as they arrive, so
    an interrupted run still leaves a usable partial baseline.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

DURATION_RE = re.compile(r"^\s*([\d.]+)s\s+\S+\s+(\S+::\S+)\s*$")
NODE_FILE_RE = re.compile(r"^(tests/[^:]+)::")
TIMEOUT_BANNER = re.compile(r"^FAILED\s+(tests/[^:]+)::.*timeout", re.IGNORECASE)


def run_batch(files: list[Path], timeout: int, root: Path) -> tuple[dict[str, float], int]:
    """Run one batch, return ({relpath: seconds}, timeouts)."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *[str(f) for f in files],
        f"--timeout={timeout}",
        "--durations=0",
        "-q",
        "--no-header",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    out = (proc.stderr or "") + "\n" + (proc.stdout or "")
    totals: dict[str, float] = defaultdict(float)
    timeouts = 0
    for line in out.splitlines():
        m = DURATION_RE.match(line)
        if m:
            seconds = float(m.group(1))
            fm = NODE_FILE_RE.match(m.group(2))
            if fm:
                rel = fm.group(1).replace("\\", "/")
                totals[rel] += seconds
            continue
        if TIMEOUT_BANNER.search(line):
            timeouts += 1
    return dict(totals), timeouts


def merge(existing: dict[str, float], additions: dict[str, float]) -> dict[str, float]:
    out = dict(existing)
    for k, v in additions.items():
        out[k] = out.get(k, 0.0) + v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default="tests")
    ap.add_argument("--output", default=".shard-timings.json")
    ap.add_argument("--batch-size", type=int, default=30, help="files per pytest invocation")
    ap.add_argument("--timeout", type=int, default=30, help="per-test timeout seconds")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    out = Path(args.output)
    existing: dict[str, float] = {}
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    seen_files: set[str] = set(existing.keys())

    all_files = sorted(p for p in root.glob("test_*.py") if p.name != "test_session_triggers.py")
    todo = [p for p in all_files if str(p.relative_to(Path.cwd())).replace("\\", "/") not in seen_files]
    if not todo:
        print(f"all {len(all_files)} files already in {out}", file=sys.stderr)
        return 0

    totals = dict(existing)
    t0 = time.perf_counter()
    for i in range(0, len(todo), args.batch_size):
        batch = todo[i : i + args.batch_size]
        batch_t0 = time.perf_counter()
        additions, timeouts = run_batch(batch, args.timeout, root)
        totals = merge(totals, additions)
        out.write_text(json.dumps(totals, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        batch_s = time.perf_counter() - batch_t0
        covered = i + len(batch)
        print(
            f"[{covered:>4}/{len(todo)}] {len(batch)} files in {batch_s:.1f}s "
            f"(timeouts={timeouts}, total {len(totals)} files, "
            f"elapsed {time.perf_counter() - t0:.1f}s)",
            file=sys.stderr,
        )

    total_s = sum(totals.values())
    print(
        f"wrote {out} — {len(totals)} files, total {total_s:.1f}s "
        f"(max {max(totals.values()):.1f}s, median {sorted(totals.values())[len(totals) // 2]:.1f}s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
