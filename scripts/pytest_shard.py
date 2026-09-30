#!/usr/bin/env python
"""List pytest files for one shard (stable order, round-robin)."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=4)
    ap.add_argument("--root", default="tests")
    args = ap.parse_args()

    files = sorted(
        p for p in Path(args.root).glob("test_*.py") if p.name != "test_session_triggers.py"
    )
    selected = [str(p) for i, p in enumerate(files) if i % args.shards == args.shard]
    print("\n".join(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
