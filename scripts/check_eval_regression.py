#!/usr/bin/env python
"""Gate nightly eval scorecards against a committed baseline.

Usage:
    check_eval_regression.py                      # compare (exit 1 on regression)
    check_eval_regression.py --update-baseline    # refresh baseline from scorecard

Design (docs/p0-guardrails-design.md §2):
- mean drop > --max-mean-drop (default 0.02 absolute)  -> FAIL
- pass_rate@0.7 drop > --max-pass-drop (default 0.05)  -> FAIL
- no baseline yet -> write one and pass (first nightly establishes it)
- regressions NEVER rewrite the baseline (no self-exemption)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_SCORECARD = Path("scorecard.json")
DEFAULT_BASELINE = Path("evals/nightly-baseline.json")


def load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        # utf-8-sig: tolerate a BOM (Windows editors / PowerShell writes);
        # reads plain UTF-8 fine too.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict) or "mean" not in data:
        print(f"error: {path} is not a scorecard dict", file=sys.stderr)
        sys.exit(2)
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--max-mean-drop", type=float, default=0.02)
    ap.add_argument("--max-pass-drop", type=float, default=0.05)
    ap.add_argument(
        "--update-baseline",
        action="store_true",
        help="Write the scorecard as the new baseline (explicit refresh only).",
    )
    args = ap.parse_args()

    scorecard = load(args.scorecard)
    if scorecard is None:
        print(f"error: scorecard not found: {args.scorecard}", file=sys.stderr)
        return 2

    mean = float(scorecard.get("mean", 0.0))
    pass_rate = float(scorecard.get("pass_rate@0.7", 0.0))
    n = scorecard.get("n", "?")

    if args.update_baseline:
        args.baseline.write_text(
            json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"baseline updated: {args.baseline} (mean={mean}, pass@0.7={pass_rate}, n={n})")
        return 0

    baseline = load(args.baseline)
    if baseline is None:
        args.baseline.write_text(
            json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"no baseline at {args.baseline}; established from this run "
              f"(mean={mean}, pass@0.7={pass_rate}, n={n})")
        return 0

    base_mean = float(baseline.get("mean", 0.0))
    base_pass = float(baseline.get("pass_rate@0.7", 0.0))
    mean_delta = mean - base_mean
    pass_delta = pass_rate - base_pass

    print(f"scorecard: mean={mean} (baseline {base_mean}, Δ{mean_delta:+.4f}), "
          f"pass@0.7={pass_rate} (baseline {base_pass}, Δ{pass_delta:+.4f}), n={n}")

    failed = False
    if mean_delta < -args.max_mean_drop:
        print(f"REGRESSION: mean dropped {abs(mean_delta):.4f} > {args.max_mean_drop}",
              file=sys.stderr)
        failed = True
    if pass_delta < -args.max_pass_drop:
        print(f"REGRESSION: pass_rate@0.7 dropped {abs(pass_delta):.4f} > {args.max_pass_drop}",
              file=sys.stderr)
        failed = True

    if failed:
        print("\nBaseline NOT auto-updated (no self-exemption). "
              "If the drop is intentional, refresh with --update-baseline "
              "or workflow_dispatch input update_baseline=true.",
              file=sys.stderr)
        return 1

    # Improvements do not silently become the new baseline either —
    # refresh stays explicit so the comparison window stays stable.
    if mean_delta > 0.005 or pass_delta > 0.01:
        print(f"note: score improved (mean {mean_delta:+.4f}); "
              "consider refreshing the baseline via --update-baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
