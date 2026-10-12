#!/bin/sh
# Enable the versioned git hooks in this repository (run once per clone).
#
# Uses core.hooksPath so the hooks live in scripts/git-hooks/ and are
# reviewed/committed like any other source — unlike .git/hooks which is
# local-only and invisible to review.

set -e
cd "$(git rev-parse --show-toplevel)"

git config core.hooksPath scripts/git-hooks
chmod +x scripts/git-hooks/* 2>/dev/null || true

echo "core.hooksPath -> $(git config core.hooksPath)"
echo "pre-commit hook active: E9 + F821 on staged .py files (docs/p0-guardrails-design.md)."
