# Archived Study Code — 2026-09

## Background

These files are leftover from two earlier architectural explorations that were
superseded by the current design but never removed from the tree:

1. **"Temporal-inspired" 5-piece suite** (streaming / checkpoint / signals /
   activity / integration) — replaced by `EventBusV2` + `langgraph.SqliteSaver`
2. **Study-side parallel implementations** (dag.py, event_store.py) —
   superseded by `core/workflow/dag.py` and `core/agent/event_store.py`

Rather than delete, the code is archived here for:
- archaeological reference (why was it abandoned?)
- potential future rollback / comparative study
- compliance with code-preservation policies

## Contents

| File | LoC | Original purpose | Reason archived |
|------|----:|------------------|-----------------|
| `streaming.py` | 294 | Temporal-style stream buffer | Replaced by `EventBusV2` |
| `checkpoint.py` | 408 | Temporal-style checkpoint mgr | Replaced by `langgraph.SqliteSaver` |
| `signals.py` | 430 | Temporal-style signal mgr | Never invoked in production |
| `activity.py` | 365 | Temporal-style activity registry | Never queried in production |
| `integration.py` | 340 | Monkey-patch legacy runner | `create_enhanced_runner()` has zero callers |
| `dag.py` | 665 | Study-side DAG engine | Replaced by `core/workflow/dag.py` |
| `event_store.py` | 492 | Study-side EventStore | **DIFFERENT** from `core/agent/event_store.py` (which is production) |
| `knowledge.py` | 106 | Knowledge extraction helpers | Runner has own `_collect_knowledge` |
| `study_io.py` | 147 | I/O helpers | Duplicates `engine_common.py` |
| `dag_engine.py` | 89 | Legacy DAG engine entry | Phase engine maps `engine='dag'` → `langgraph`; see `phase_engine.py:139-143` |
| `runner_context.py` | 38 | RunnerContext DI dataclass | `_to_context()` was never called; extracted in 2026-09 from `runner.py:201-220` |
| `chat_mutex_legacy.py` | ~30 | v1 chat/study cooperative mutex | Replaced by v2 single-identity; see class docstring in file |

**Total: 3,373 lines** archived.

## Important

- **DO NOT import from this directory in production code**
- Files here are **NOT maintained** and may be broken in isolation
- Several files have module-level side effects (e.g., `signals.py` registers
  handlers, `activity.py` populates a registry) — these were safe in the old
  tree but should not be relied upon
- For current architecture, see `core/study/{runner,scheduler,phase_engine,langgraph_engine}.py`

## Archived Test Files

| File | LoC | |
|------|----:|-|
| `tests/attic/attic_test_study_integration.py` | ~250 | Tests the 5-piece suite |
| `tests/attic/attic_test_study_infrastructure.py` | ~475 | Tests the integration cluster |

The `tests/conftest.py` has `collect_ignore_glob = ["attic/*"]` so pytest
will not auto-collect these. They remain in git history and on disk for
archaeological reference.