# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-07-22

### Added

#### P3-a: Goal subsystem (research-only ledger)
- `core/goal/`: GoalStatus (12 lifecycle values), RiskTier (4 values
  including LIVE_TRADING_OR_EXECUTION which is rejected at create),
  GoalRecord + GoalClaim + GoalCriterion + EvidenceInput/Record + AuditRow
  + StaleGoalError — all frozen dataclasses.
- `core/goal/policy.py`: normalize_required_text + reject_live_execution_objective
  (rejects English + Chinese live-trading phrases).
- `core/goal/store.py`: SQLite-backed GoalStore at
  `~/.quantnodes-research/goals.db` with WAL + busy_timeout + foreign keys.
- `core/goal/context.py`: format_goal_context + format_goal_continuation_prompt.
- `core/goal/cli.py`: 7 subcommands (start / status / evidence / audit /
  complete / list / cancel).
- `quantnodes-research goal ...` CLI surface.

#### P3-b: Hypothesis subsystem
- `core/hypothesis/registry.py`: File-backed JSON registry at
  `~/.quantnodes-research/hypotheses.json` (atomic write, malformed
  raises ValueError).
- `core/hypothesis/auto_create.py`: HypothesisAutoCreator — idempotent
  helper that creates an `exploring` hypothesis on first AgentLoop call
  per (strategy, market).
- 5 statuses: exploring / testing / validated / rejected / monitoring.
- SHA-256-derived `hyp_<12hex>` IDs with collision suffix.
- Token-overlap search across all serialized fields + recency tiebreak.
- `core/hypothesis/cli.py`: 6 subcommands (create / list / show / update
  / search / link).
- `quantnodes-research hypothesis ...` CLI surface.

#### P3-c: Validation toolkit
- `core/validation/market.py`: MarketType enum (7 values) +
  bars_per_year + warn_if_unsupported_market (UserWarning for
  non-supported markets).
- `core/validation/trade_input.py`: Lightweight TradeInput dataclass.
- `core/validation/monte_carlo.py`: Monte Carlo permutation test.
- `core/validation/bootstrap.py`: Bootstrap Sharpe CI.
- `core/validation/walk_forward.py`: Walk-Forward analysis.
- `core/validation/runner.py`: Orchestrator with multi-market support.
- `core/validation/utils.py`: `_json_safe` (NaN/inf → None), `_sharpe`.
- `core/validation/cli.py`: `validate-run <run_dir>` command.
- `quantnodes-research validate-run ...` CLI surface.

#### P3-d: Integration
- AgentLoop gained `session_id`, `strategy_name`,
  `enable_goal_injection`, `enable_hypothesis_auto_create` parameters
  (all optional, backward-compatible).
- AgentLoop.run() now auto-creates an exploring hypothesis on first
  call per (strategy, market) and injects the current
  `<current-research-goal>` block into the user message.
- Workflow layer integration pending (Week 14).

#### Documentation
- `docs/goal-design.md` — comprehensive design doc for the Goal
  subsystem (10 sections, including data model + CLI + limitations).
- `docs/validation-design.md` — multi-market roadmap + current
  support matrix.

### Changed
- AgentLoop __init__ signature now includes 4 new optional params.
- CLI surface gained 14 new subcommands (7 goal + 6 hypothesis + 1 validate-run).

### Tests
- Total tests: 3,770 → 4,053 (+283 new tests).
- New test files: test_goal_* (6), test_hypothesis*.py (2),
  test_validation_*.py (6), test_p3_*.py (3).

## [0.2.0] - 2026-07-22

### Added
- Hook system (P2-a): UnifiedHook (16 events) + AgentHook (13 events).
- Memory FTS5 (P2-c): global index + recency boost + write dedup.
- Session management (P2-d): SQLite + FTS5 + triggers + rate limiter.
- CLI integration (P2-e): session stats / list subcommands.

## [0.1.0] - 2026-07-22

### Added
- Initial release: workspace init, baseline backtest, AgentLoop, 6 tools.

[0.3.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.3.0
[0.2.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.2.0
[0.1.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.1.0