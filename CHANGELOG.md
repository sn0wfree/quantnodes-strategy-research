# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-07-24

### Added (Rich CLI / vibe-trading parity)
- **`cli.theme`** — Rich stylesheet + dark-mode detection (`is_dark()`, `force_dark()`).
- **`cli.utils.format`** — `format_duration(ms/s)`, `format_tokens(n)`, `abbreviate_num(n, currency=)`
  with banker's rounding + 3-decimal precision.
- **`cli.utils.thinking_verbs`** — verb pool + `pick_thinking_verb(seed=)` for seeded determinism.
- **`cli.components`** —
  - `WorkingIndicator` (`ThinkingSpinner`).
  - `tool_event.py` — `beautify_tool_name`, `summarize_args`, `render_tool_event(s)`.
  - `hint_bar.py` — left+right hint bar with overflow truncation.
  - `chat_log.py` — turn replay rendering.
- **`cli.ui`** —
  - `banner.py` — 8-line gradient logo + version line, `#258BFF→#A5CFFF` lerp.
  - `transcript.py` — markdown answer renderer (pipe-table upgrade, **bold**/*italic*/`code`/--- strip).
  - `rail.py` — `RailRunDashboard` event dispatcher (tool_call/text_delta/tool_progress/
    tool_heartbeat/tool_result/thinking_done/llm_usage/compact).
- **`cli.commands`** —
  - `slash_router.py` — 16-entry `SLASH_COMMANDS` registry, fuzzy matcher
    (prefix > substring > subsequence), `_ALIASES` (q/exit/:q → quit, ? → help),
    `match_commands` (bare `/` returns full list).
  - `help.py` — `render_help_table`: commands grid + shortcuts grid.
  - `show.py` — `/show`, `/pine`, `/skill` with `_locate_run`.
  - `slash_session.py` — `/history`, `/search`, `/export`.
  - `slash_memory.py` — `/memory list/show/search/forget` (refuses when `yes=False`).
  - `slash_goal.py` — `/goal status/start/evidence/complete/cancel/help`.
  - `slash_chat.py` — `/model`, `/clear(ctx)`, `/quit → 2`, `/debug(ctx)` toggle,
    `/journal`, `/shadow` (queues prompt on `ctx.pending_prompt`).
  - `slash_halt.py` — bare-word kill switch (`停/停手/stop/kill/halt`) + `/resume/continue/go`.
- **`cli.interactive`** —
  - `completer.py` — `SlashCompleter(max_suggestions=8)` for prompt_toolkit.
  - `main.py` — `InteractiveContext` dataclass, `_DISPATCH` table (16 handlers),
    `dispatch_slash()`, `process_turn()` (single-turn driver, halt/resume intercept,
    proposal pick intercept, plain text → `ctx.history`), `main(argv)` (`--banner` flag).
- **`cli.onboard`** — 5-step onboarding wizard (provider → model → key → timeout →
  optional tushare). `Provider` × 5, `BACK`/`CANCEL` sentinels, `run_onboarding(env_dir,
  inputs, skip_tushare)`, `is_onboarded(env_dir)`. Skips step 3 if `key_env is None`.
- **`cli.halt`** — thread-safe `HALT` sentinel + `trip_halt(reason=)`/`clear_halt()`/
  `is_halted()`/`require_not_halted(operation=)` + `HaltError`.
- **`cli.mandate`** — research-proposal intercept: `Proposal`, `make_proposal`,
  `is_pick(input, proposal)`, `capture_pick(input, proposal)` returning
  `{index, label, payload, context}` or `None`, `has_pending_proposal(ctx)`.
- **Entry dispatcher** — `cli/__main__.main()` picks Rich REPL when argv is empty or
  contains only `--banner`; otherwise delegates to argparse `cli.main()`. New
  console_script: `quantnodes-research` (v0.4.0).

### Fixed
- `cli.onboard.run_onboarding`: step 1 now consumes one input (not N), fixing the
  "provider loop reads too many lines" bug.
- `cli.slash_goal`: `append_evidence` switched from positional to keyword-only
  `session_id/goal_id/expected_goal_id/evidence` to match store API.

### Tests
- +484 new tests (`5683 → 6167`). All CLI modules now have dedicated suites.

## [0.4.0] - 2026-07-23

### Added
- **P3-B/C/D/E unit test coverage** — 117 new tests.
  - `tests/test_goal_p3b.py`: progress_percent, decompose_goal, sub/parent goals (13).
  - `tests/test_hypothesis_p3c.py`: VALID_TRANSITIONS, derive/link/contradicts (35).
  - `tests/test_hypothesis_store.py`: SQLite CRUD, FTS5 search, JSON migration (37).
  - `tests/test_hypothesis_validator.py`: validate_hypothesis auto-validation pipeline (20).
  - `tests/test_goal_hook_p3d.py`: _on_goal_complete hook, autoresearch CLI helpers (12).
- **HypothesisStore concurrency smoke tests** — 5 tests for parallel create/update/search.
  - `tests/test_hypothesis_store_concurrent.py`.
- **API router behavior tests** — 18 new tests in `tests/test_api.py`: goal
  list/evidence/complete, hypothesis create/list/search/update.

### Fixed
- **HypothesisStore concurrency safety**: `create()` and `update()` now hold
  `self._lock` across the entire method body (SELECT + write). Previously,
  releasing the lock between SELECT and BEGIN IMMEDIATE caused
  `OperationalError('cannot start a transaction within a transaction')`
  under parallel writes.
- **API router error codes**:
  - `hypothesis_update`: returns 404 for missing ID (was 500).
  - `goal_complete`: returns 409 for stale goal (was 500), 400 for
    invalid state (was 500).
- **API router alignment with P3 stores**:
  - `goal_list` actually calls `store.list_goals()` (was hardcoded `[]`).
  - `goal_evidence` uses `EvidenceInput` + `append_evidence()` (was
    deprecated `add_evidence`).
  - `goal_complete` uses `update_status()` (was deprecated
    `transition_status`).
  - `hypothesis_create` accepts `universe`/`signal_definition` (was
    legacy `tags`/`metadata`).
  - Hypothesis serialization uses `to_dict()` (was `__dict__`, which
    broke datetime JSON serialization).

### Changed
- `GoalStore._on_goal_complete`: added missing `logger` import.
- `HypothesisStore._migrate_from_json`: JSON fallback path now derives
  from `db_path.parent` (was hardcoded `~/.quantnodes-research/`),
  enabling test isolation via `tmp_path`.

### Tests
- Total tests: 5,491 → 5,631 (+140 new tests).

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

[0.4.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.4.0
[0.3.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.3.0
[0.2.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.2.0
[0.1.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.1.0