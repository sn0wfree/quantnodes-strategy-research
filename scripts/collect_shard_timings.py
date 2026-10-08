#!/usr/bin/env python
"""Build a `.shard-timings.json` of {relpath: total_seconds} by aggregating
function-level durations from `pytest --durations=0`.

Used as the input to `pytest_shard.py --mode time-aware`. Run on a green tree;
flaky timing data is worse than no data.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# pytest --durations=0 prints lines like:
#   0.12s call     tests/test_loop.py::test_foo
#   1.03s setup    tests/test_loop.py::test_bar
# We want to sum every duration line whose target is a tests/test_*.py node.
DURATION_RE = re.compile(r"^\s*([\d.]+)s\s+\S+\s+(\S+::\S+)\s*$")
NODE_FILE_RE = re.compile(r"^(tests/[^:]+)::")


def collect(root: Path, extra_args: list[str]) -> dict[str, float]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(root),
        "--durations=0",
        "-q",
        "--no-header",
        # test_session_triggers.py is excluded by pytest_shard.py too —
        # known slow / flaky magnet, runs in its own nightly job instead.
        "--ignore=tests/test_session_triggers.py",
        # Don't bail on first failure: a single broken test would otherwise
        # leave 99% of files without timing data, defeating the whole point
        # of the collector.
        *extra_args,
    ]
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    # parse stderr (pytest writes --durations to stderr)
    out = (proc.stderr or "") + "\n" + (proc.stdout or "")
    totals: dict[str, float] = defaultdict(float)
    for line in out.splitlines():
        m = DURATION_RE.match(line)
        if not m:
            continue
        seconds = float(m.group(1))
        nodeid = m.group(2)
        file_m = NODE_FILE_RE.match(nodeid)
        if not file_m:
            continue
        rel = file_m.group(1).replace("\\", "/")
        totals[rel] += seconds
    return dict(totals)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default="tests")
    ap.add_argument("--output", default=".shard-timings.json")
    ap.add_argument(
        "--extra",
        nargs=argparse.REMAINDER,
        default=[],
        help="extra args passed to pytest (after --)",
    )
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 1

    timings = collect(root, args.extra)
    if not timings:
        print(
            "warning: no durations parsed; is pytest installed and tests collecting?",
            file=sys.stderr,
        )
        return 1

    out = Path(args.output)
    out.write_text(json.dumps(timings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    total = sum(timings.values())
    print(
        f"wrote {out} — {len(timings)} files, total {total:.1f}s "
        f"(max {max(timings.values()):.1f}s, median {sorted(timings.values())[len(timings) // 2]:.1f}s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
