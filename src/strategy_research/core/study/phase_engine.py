"""Phase engine — round execution via phase-split functions.

Extracted from runner.py to reduce file size and improve modularity.
Uses run_researcher_phase / run_execution_phase / run_evaluation_phase
from autoresearch.py with AEGIS hooks injected between phases.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .engine_common import safe_json_loads, build_agent_ctx, phase_emitter, save_agent_outputs
from .metric_targets import meets_metric_targets, metric_pass_set, acceptance_config_from_targets
from .runner import SR_AGENT_MAX_ITER

logger = logging.getLogger(__name__)


def run_round_phases(
    runner: Any,
    round_num: int,
    previous_summary: dict | None,
    directive_text: str | None,
) -> dict:
    """Execute one round using phase-split functions with AEGIS hooks.

    This is the main entry point for the phase engine, extracted from
    runner._run_one_round_impl.
    """
    from strategy_research.core.autoresearch import (
        _create_run_dir,
        generate_run_summary,
        read_current_state,
        run_evaluation_phase,
        run_execution_phase,
        run_researcher_phase,
        save_run_summary,
    )
    from strategy_research.core.study import review_loop as rl
    from strategy_research.core.study import round_manifest as rm
    from strategy_research.core.study import state_store as ss

    study = runner._get_study()
    sid = study.study_id
    session = study.session_id
    path = Path(study.workspace_path).resolve()
    strategy = study.strategy_name
    metric_targets = study.metric_targets
    root = ss.study_root(path, sid)
    state = ss.load(path, sid)

    # ── v2 round-start knowledge gap check (design §11.1) ─────
    knowledge_path = root / "knowledge.md"
    knowledge_text = (
        knowledge_path.read_text(encoding="utf-8")
        if knowledge_path.exists() else ""
    )
    next_focus = ""
    if state.last_review:
        next_focus = str(state.last_review.get("next_focus") or "")
    gap_topics = rl.gap_check(study.objective, next_focus, knowledge_text)
    if gap_topics:
        runner._collect_knowledge(gap_topics)
    runner._emit(session, "study_knowledge_check", {
        "study_id": sid, "round": round_num,
        "gap_topics": gap_topics, "collected": bool(gap_topics),
    })

    # ── round dir + inherited strategy copy ─────────────────────
    round_dir = rm.round_dir(path, sid, round_num)
    round_dir.mkdir(parents=True, exist_ok=True)

    runs_dir, run_name, run_dir = _create_run_dir(
        path, strategy, runs_dir=round_dir,
    )
    adopted = rm.resolve_adopted_run_for_start(state.last_keep_run_dir)
    inherited_from = adopted
    src_strategy = (root / adopted / "strategy.py") if adopted != "baseline" \
        else root / "baseline" / "strategy.py"
    dst_strategy = run_dir / "strategy.py"
    if src_strategy.exists():
        dst_strategy.write_text(
            src_strategy.read_text(encoding="utf-8"), encoding="utf-8",
        )
    src_cfg = root / adopted / "config.yaml" if adopted != "baseline" \
        else root / "baseline" / "config.yaml"
    if src_cfg.exists():
        (run_dir / "config.yaml").write_text(
            src_cfg.read_text(encoding="utf-8"), encoding="utf-8",
        )

    results_tsv = root / "results.tsv"

    # ── Load the execution graph (topology-aware scheduling) ────
    graph = runner._load_graph(path, sid)

    # read state
    current_state = read_current_state(
        path, strategy,
        strategy_file=dst_strategy,
        results_tsv=results_tsv,
    )
    current_state["study_strategy_path"] = str(
        round_dir.relative_to(path) / run_name / "strategy.py"
    )

    # AEGIS: inject journal + scoreboard context
    journal_ctx = runner._build_journal_context()
    scoreboard_ctx = runner._build_scoreboard_context()
    if journal_ctx:
        current_state["journal_context"] = journal_ctx
    if scoreboard_ctx:
        current_state["lever_scoreboard"] = scoreboard_ctx

    # v2 guidance: human decision points injected every round
    from strategy_research.core.study import guidance as gd
    guidance = gd.load_guidance(path, sid)
    guidance_section = gd.render_guidance_section(guidance)
    if guidance_section:
        current_state["human_guidance"] = guidance_section

    # Inject factor failures from previous round
    if previous_summary and previous_summary.get("factor_failures"):
        current_state["factor_failures"] = previous_summary["factor_failures"]

    # ── Engine dispatch ──────────────────────────────────────
    engine = getattr(study, "engine", None) or "phases"
    logger.info("phase_engine: round %d, engine=%s, study.engine=%s", round_num, engine, getattr(study, "engine", "NOT_SET"))
    if os.environ.get("SR_STUDY_DAG_ENGINE") == "1" and engine == "phases":
        # Backward compat: the legacy env var opts into the graph-based
        # engine. It maps to "langgraph" — the only graph dispatch the
        # engine switch below understands ("dag_engine" has no
        # production callers; see phase_engine dispatch).
        engine = "langgraph"
    if engine == "dag":
        # store/API still accept engine='dag' (M12), but no dag executor
        # exists in production — map to langgraph instead of silently
        # running the phases engine.
        engine = "langgraph"

    if engine == "langgraph":
        lg_result = runner._run_round_via_langgraph(
            path, strategy, current_state, run_dir, graph,
            session=session, sid=sid, round_num=round_num,
            directive_text=directive_text,
        )
        # HITL approval pause — return early, runner will re-enter on resume
        if lg_result.get("paused_for_approval"):
            return lg_result
        # Novelty gate rejected (graph routed to END before any
        # downstream agent ran) — return before finalization; the runner
        # loop sees ``aborted`` and skips the round, mirroring the
        # phases branch below.
        if lg_result.get("aborted"):
            return lg_result

        # Map langgraph result to the variables expected by the
        # shared finalization pipeline below.
        researcher_output = lg_result.get("researcher_output", {})
        # Defense: researcher may return a plain string on max_iter —
        # never let a non-dict reach the .get() calls below.
        if not isinstance(researcher_output, dict):
            researcher_output = {}
        strategist_output = lg_result.get("strategist_output", {})
        metrics = lg_result.get("metrics", {})
        verdict = lg_result.get("verdict", "discard")
        decision = lg_result.get("decision")
        backtest_error = lg_result.get("backtest_error")

        agent_outputs = {"researcher": researcher_output}
        for _k in (
            "data_quality_output", "factor_analyst_output",
            "strategist_output", "portfolio_construction_output",
            "risk_controller_output", "attribution_analyst_output",
            "anti_overfit_analyst_output", "backtest_diagnostics_output",
        ):
            if _k in lg_result:
                agent_outputs[_k] = lg_result[_k]

        hypothesis = researcher_output.get("hypothesis", "")
        predicted_affected = (
            researcher_output.get("predicted_affected")
            or [t["name"] for t in metric_targets]
        )
        # Claims capture: predictions come from the researcher's own
        # reasoning (they never see the backtest result), so they stay
        # falsifiable even though this engine runs the backtest in-graph.
        captured_predictions = _capture_round_claims(
            runner, round_num, researcher_output, predicted_affected, metric_targets,
        )

        # Novelty dedup on this branch is handled in-graph by the
        # novelty_gate node (built when profile.hitl is enabled — the
        # "langgraph" profile always is, see langgraph_engine profiles).
        # The graph aborts before any downstream agent runs, and
        # _run_round_via_langgraph re-attaches ``aborted`` to its result.

        # Shim dicts so finalization references like
        # eval_result["decision"] and exec_result.get("backtest_error")
        # resolve without AttributeError.
        eval_result = {"decision": decision, "aoa_llm_verdict": lg_result.get("aoa_llm_verdict")}
        exec_result = {"backtest_error": backtest_error}
        captured_predictions = {}

    else:
        # Phase 1: researcher
        runner._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "researcher", "status": "started",
        })
        researcher_result = run_researcher_phase(
            path, strategy, current_state, run_dir,
            session_id=session, run_name=run_name,
            behavior=study.behavior, max_retries=3,
            max_iterations=SR_AGENT_MAX_ITER,
            directives=directive_text,
            lazy_detection_interval=study.lazy_detection_interval,
            keep_recent=study.keep_recent, round_num=round_num,
            runs_dir=runs_dir,
            loop_strategy=runner._loop_strategy,
        )
        runner._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "researcher", "status": "done",
        })
        researcher_output = researcher_result["researcher_output"]
        # Defense: same non-dict guard as the langgraph branch above.
        if not isinstance(researcher_output, dict):
            researcher_output = {}

        hypothesis = researcher_output.get("hypothesis", "")
        predicted_affected = researcher_output.get("predicted_affected") or [t["name"] for t in metric_targets]
        if not _novelty_gate(runner, round_num, hypothesis, predicted_affected):
            return {"round": round_num, "run_name": run_name, "aborted": True,
                    "reason": "novelty_rejected"}

        # Claims capture: BEFORE the backtest so predictions are credible.
        captured_predictions = _capture_round_claims(
            runner, round_num, researcher_output, predicted_affected, metric_targets,
        )

        # Phase 2: execution
        runner._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "execution", "status": "started",
        })
        exec_result = _run_execution_with_parse_retry(
            runner, path, strategy, current_state, researcher_output, run_dir,
            session=session, run_name=run_name,
            results_tsv=results_tsv, round_num=round_num,
        )
        runner._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "execution", "status": "done",
        })
        metrics = exec_result["metrics"]
        strategist_output = exec_result["strategist_output"]

        # Phase 3: evaluation
        runner._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "evaluation", "status": "started",
        })
        eval_result = run_evaluation_phase(
            path, strategy, exec_result["backtest_result"], metrics, run_dir,
            behavior=study.behavior, max_retries=3,
            max_iterations=SR_AGENT_MAX_ITER,
            loop_strategy=runner._loop_strategy,
        )
        runner._emit(session, "study_phase", {
            "study_id": sid, "round": round_num, "phase": "evaluation", "status": "done",
        })
        verdict = eval_result["verdict"]

        agent_outputs = {
            "researcher": researcher_output,
            **{k: exec_result.get(k) for k in (
                "data_quality_output", "factor_analyst_output",
                "strategist_output", "portfolio_construction_output",
            )},
            **{k: eval_result.get(k) for k in (
                "risk_controller_output", "attribution_analyst_output",
                "anti_overfit_analyst_output", "backtest_diagnostics_output",
            )},
        }
        backtest_error = exec_result.get("backtest_error")

    # ── guidance gates hard check (shared) ────────────────────
    gate_violations = _check_guidance_gates(guidance, metrics)
    if gate_violations:
        verdict = "discard"

    # E2 completion semantics (shared)
    e2_passed = bool(
        study.metric_targets
        and meets_metric_targets(metrics, study.metric_targets)
        and verdict == "keep"
        and not gate_violations
    )

    # disk: results.tsv + summary
    runner._update_results_tsv(
        runs_dir, run_name, verdict,
        round_num=round_num, results_tsv=results_tsv,
    )
    summary = generate_run_summary(agent_outputs, metrics, verdict, round_num, previous_summary)
    summary["acceptance_decision"] = eval_result["decision"].to_dict()
    save_run_summary(run_dir, summary)

    # Emit final graph topology
    runner._emit_topology(session, sid, round_num, graph, agent_outputs)

    # AEGIS: Attribution + Journal + Regression Gate
    passed_now = metric_pass_set(metrics, metric_targets)
    from .attribution import classify_attribution
    attribution = classify_attribution(predicted_affected, runner._prev_passed, passed_now)

    lever = strategist_output.get("action", "unknown") if isinstance(strategist_output, dict) else "unknown"
    gating_outcome = "accepted" if verdict == "keep" else "reverted"
    _record_journal_and_regression(
        runner, round_num, hypothesis, predicted_affected, lever,
        strategist_output, gating_outcome, attribution,
        predictions=captured_predictions, metrics=metrics,
    )

    # AEGIS: Scoreboard
    from ..goal.scoreboard import LeverScoreboard
    if not hasattr(runner, "_scoreboard"):
        runner._scoreboard = LeverScoreboard()
    runner._scoreboard.update([lever], attribution, gating_outcome, round_num, round_num)

    # ── v2 artifacts: manifest + summary.md + journal.md ──
    from .aegis import verdict_reason as _verdict_reason
    vreason = _verdict_reason(eval_result, strategist_output)
    if gate_violations:
        gate_reason = "guidance gates: " + ",".join(v["id"] for v in gate_violations)
        vreason = f"{vreason} | {gate_reason}" if vreason else gate_reason
    strategy_changes = (
        strategist_output.get("changes")
        if isinstance(strategist_output, dict) else None
    )
    manifest = rm.build_manifest(
        round_num=round_num,
        inherited_from=inherited_from,
        adopted_run=state.last_keep_run_dir,
        run_name=run_name,
        hypothesis=hypothesis,
        levers=[lever],
        predicted_affected=predicted_affected,
        strategy_changes=strategy_changes,
        metrics=metrics,
        prev_metrics=summary.get("performance_change") or None,
        baseline_metrics=state.baseline_best or None,
        verdict=verdict,
        verdict_reason=vreason,
        gates=gate_violations or None,
        budget={
            "turns_used": runner._total_used_turns,
            "time_used_s": round(runner._total_used_time, 1),
            "total": {
                "turns": study.budget_turn,
                "time_s": study.budget_time_seconds,
            },
        },
    )
    rm.save_manifest(manifest, path, sid, round_num)
    summary_md = rm.render_round_markdown(manifest, study.objective)
    (rm.summary_path(path, sid, round_num)).write_text(
        summary_md, encoding="utf-8",
    )
    rm.append_journal_md(path, sid, manifest, study.objective)

    # ── state.json update ────────────────────────────────────
    _update_round_state(
        runner, path, sid, round_num, run_name, verdict, metrics, state, strategy_changes,
    )

    # ── goal ledger: keep-round evidence ─────────────────────
    if verdict == "keep" and study.goal_id:
        runner._record_keep_evidence(round_num, run_name, metrics)

    # ── v2 review cycle ──────────────────────────────────────
    # §4b short-circuit: if upstream agents produced max_iterations
    # placeholders instead of structured output, skip the reviewer LLM —
    # reviewing garbage wastes calls and feeds the failure cascade.
    from .scenario_router import detect_max_iter_placeholders
    short_circuited = detect_max_iter_placeholders(agent_outputs)
    if short_circuited:
        logger.warning(
            "round %d short-circuit review: max_iter placeholders in %s",
            round_num, short_circuited,
        )
        runner._emit(session, "study_phase", {
            "study_id": sid, "round": round_num,
            "phase": "review", "status": "short_circuited",
            "agents": short_circuited,
            "reason": "upstream_failed",
        })
        review_stop = None
    else:
        review_stop = _run_review_cycle(
            runner, round_num, manifest, state, verdict, hypothesis,
        )

    return {
        "round": round_num, "run_name": run_name, "run_dir": run_dir,
        "metrics": metrics, "verdict": verdict,
        "decision": eval_result["decision"].to_dict(),
        "agent_outputs": agent_outputs, "summary": summary,
        "backtest_error": backtest_error,
        "passed_now": passed_now,
        "manifest": manifest,
        "state": state,
        "review_stop": review_stop,
        "e2_passed": e2_passed,
    }


# ── Helper functions ──────────────────────────────────────────────

def _check_guidance_gates(guidance: Any, metrics: dict) -> list[dict]:
    """Hard-check guidance gates; returns violating gate dicts."""
    from strategy_research.core.study import guidance as gd
    violations: list[dict] = []
    if not guidance.gates:
        return violations
    found, skipped = gd.check_violations(guidance.gates, metrics)
    for gid in skipped:
        logger.warning("guidance gate %s skipped (metric missing): %s", gid, metrics)
    return found if found else violations


def _novelty_gate(
    runner: Any,
    round_num: int,
    hypothesis: str,
    predicted_affected: list,
) -> bool:
    """Run the AEGIS novelty gate; returns True when the round proceeds."""
    study = runner._get_study()
    is_novel, novelty_reason = runner._check_novelty(hypothesis, predicted_affected)
    if is_novel:
        return True
    runner._archive_rejected(round_num, hypothesis, "novelty", novelty_reason)
    runner._emit(study.session_id, "study_round_rejected", {
        "study_id": study.study_id, "round": round_num,
        "reason": "novelty", "detail": novelty_reason,
    })
    return False


def _capture_round_claims(
    runner: Any,
    round_num: int,
    researcher_output: Any,
    predicted_affected: list,
    metric_targets: list,
) -> dict:
    """Capture falsifiable predictions BEFORE the backtest runs.

    Two-phase journal write (phase A): append the entry now with the
    researcher's ``predictions`` object; attribution/levers/prediction
    outcomes are backfilled later (phase B, ``_record_journal_and_regression``).

    Claims are optional by design (graceful degradation): missing or
    malformed predictions simply store ``{}`` — never fails the round.
    """
    from .claims import normalize_predictions
    if not isinstance(researcher_output, dict):
        return {}
    study = runner._get_study()
    known = [t["name"] for t in metric_targets]
    predictions = normalize_predictions(
        researcher_output.get("predictions"), known_metrics=known,
    )
    hypothesis = researcher_output.get("hypothesis", "") or ""
    try:
        runner._goal_store.append_journal_entry(
            study.goal_id, study.session_id, round_num,
            hypothesis, hypothesis[:60],
            levers=[], predicted_affected=predicted_affected,
            predictions=predictions,
        )
    except Exception:  # noqa: BLE001 — claims tracking must not break rounds
        logger.warning("claims capture failed for round %d", round_num, exc_info=True)
        return {}
    if predictions:
        runner._emit(study.session_id, "study_claims_captured", {
            "study_id": study.study_id, "round": round_num,
            "predictions": predictions,
        })
    return predictions


def _record_journal_and_regression(
    runner: Any,
    round_num: int,
    hypothesis: str,
    predicted_affected: list,
    lever: str,
    strategist_output: Any,
    gating_outcome: str,
    attribution: Any,
    predictions: dict | None = None,
    metrics: dict | None = None,
) -> None:
    """AEGIS: backfill journal entry (phase B) + run the regression gate.

    The entry was appended pre-backtest by ``_capture_round_claims`` with
    the researcher's predictions. Here we fill attribution/levers/changeset
    and validate predictions against the actual metrics. When no captured
    entry exists (e.g. HITL resume path), fall back to append+fill so the
    AEGIS journal never loses the round.
    """
    from .claims import validate_predictions
    study = runner._get_study()
    session = study.session_id
    changeset = strategist_output.get("changes") if isinstance(strategist_output, dict) else None
    filled = runner._goal_store.fill_journal_attribution(
        study.goal_id, session, round_num, gating_outcome, attribution,
        levers=[lever], changeset=changeset,
    )
    if not filled:
        # No pre-backtest capture for this round — append now.
        runner._goal_store.append_journal_entry(
            study.goal_id, session, round_num, hypothesis, hypothesis[:60],
            levers=[lever], predicted_affected=predicted_affected,
            changeset=changeset,
        )
        runner._goal_store.fill_journal_attribution(
            study.goal_id, session, round_num, gating_outcome, attribution,
        )
    if predictions:
        try:
            outcome = validate_predictions(predictions, metrics)
            if outcome:
                runner._goal_store.fill_journal_prediction_outcome(
                    study.goal_id, session, round_num, outcome,
                )
                runner._emit(session, "study_claims_validated", {
                    "study_id": study.study_id, "round": round_num,
                    "outcome": outcome,
                })
        except Exception:  # noqa: BLE001 — claims tracking must not break rounds
            logger.warning("claims validation failed for round %d", round_num, exc_info=True)
    passes, regressed = runner._check_regression(attribution)
    if not passes:
        runner._archive_rejected(round_num, hypothesis, "regression", str(regressed))
        runner._emit(session, "study_round_rejected", {
            "study_id": study.study_id, "round": round_num,
            "reason": "regression", "regressed": regressed,
        })


def _update_round_state(
    runner: Any,
    path: Path,
    sid: str,
    round_num: int,
    run_name: str,
    verdict: str,
    metrics: dict,
    state: Any,
    strategy_changes: Any,
) -> None:
    """Persist state.json + DB mirror for the completed round."""
    from strategy_research.core.study import state_store as ss

    state.last_completed_round = round_num
    if verdict == "keep":
        state.last_keep_run_dir = f"rounds/round_{round_num:04d}/{run_name}"
        state.discard_streak = 0
        for key in ("calmar", "sharpe", "max_dd"):
            if key in metrics and isinstance(metrics.get(key), (int, float)):
                if (state.best_metrics.get(key, float("-inf")) or float("-inf")) < metrics[key]:
                    state.best_metrics[key] = metrics[key]
    else:
        state.discard_streak += 1
    state.budget_used_turns = runner._total_used_turns
    state.budget_used_time_s = round(runner._total_used_time, 1)
    ss.save(path, sid, state)

    # DB mirror: study_rounds row
    try:
        runner.study_store.append_round(
            sid, round_num, run_name,
            metrics=metrics, verdict=verdict,
            config_changes=strategy_changes,
        )
    except Exception as exc:
        logger.warning("append_round failed (mirror, retry): %s", exc)
        try:
            runner.study_store.append_round(
                sid, round_num, run_name,
                metrics=metrics, verdict=verdict,
                config_changes=strategy_changes,
            )
        except Exception as exc2:
            logger.warning("append_round retry failed (mirror): %s", exc2)


def _run_execution_with_parse_retry(
    runner: Any,
    path: Path,
    strategy: str,
    current_state: dict,
    researcher_output: dict,
    run_dir: Path,
    *,
    session: str,
    run_name: str,
    results_tsv: Path,
    round_num: int,
) -> dict:
    """Wrap run_execution_phase with parse_failed auto-recovery."""
    from ..autoresearch import run_execution_phase
    from .runner import _parse_failed_agents, SR_AGENT_MAX_PARSE_RETRIES, SR_AGENT_PARSE_BACKOFF_BASE, SR_AGENT_PARSE_BACKOFF_MAX

    study = runner._get_study()
    last_failed_agents: list[str] = []

    for attempt in range(SR_AGENT_MAX_PARSE_RETRIES):
        result = run_execution_phase(
            path, strategy, current_state, researcher_output, run_dir,
            session_id=session, run_name=run_name,
            behavior=study.behavior, max_retries=3,
            max_iterations=SR_AGENT_MAX_ITER,
            strategy_dir=run_dir,
            results_tsv=results_tsv,
            round_num=round_num,
            loop_strategy=runner._loop_strategy,
        )
        last_failed_agents = _parse_failed_agents(result)
        if not last_failed_agents:
            return result
        if result.get("strategist_output"):
            logger.warning(
                "Round %d partial parse_failed (%s) but strategist OK; "
                "continuing without retry",
                round_num, last_failed_agents,
            )
            return result
        if attempt < SR_AGENT_MAX_PARSE_RETRIES - 1:
            delay = min(
                SR_AGENT_PARSE_BACKOFF_BASE * (2 ** attempt),
                SR_AGENT_PARSE_BACKOFF_MAX,
            )
            logger.warning(
                "Round %d parse_failed agents=%s, retrying in %ds "
                "(attempt %d/%d)",
                round_num, last_failed_agents, delay,
                attempt + 1, SR_AGENT_MAX_PARSE_RETRIES,
            )
            runner._emit(session, "study_parse_retry", {
                "study_id": study.study_id,
                "round": round_num,
                "failed_agents": last_failed_agents,
                "delay_s": delay,
                "attempt": attempt + 1,
                "max_attempts": SR_AGENT_MAX_PARSE_RETRIES,
            })
            time.sleep(delay)

    logger.error(
        "Round %d parse_failed after %d retries (%s); counting as idle",
        round_num, SR_AGENT_MAX_PARSE_RETRIES, last_failed_agents,
    )
    runner._idle_rounds += 1
    return result


def _run_review_cycle(
    runner: Any,
    round_num: int,
    manifest: dict,
    state: Any,
    verdict: str,
    hypothesis: str,
) -> str | None:
    """Inter-round review: deviation tracking, info collection, todos."""
    from strategy_research.core.agent.role_factory import (
        run_agent_via_llm,
        should_use_real_llm,
    )
    from strategy_research.core.study import review_loop as rl
    from strategy_research.core.study import round_manifest as rm
    from strategy_research.core.study import state_store as ss
    from .runner import ShutdownReason, SR_STUDY_MAX_DEVIATION, SR_STUDY_COLLECT_INTERVAL

    study = runner._get_study()
    sid = study.study_id
    session = study.session_id
    path = Path(study.workspace_path).resolve()
    root = ss.study_root(path, sid)
    todos_path = root / "todos.md"
    knowledge_path = root / "knowledge.md"
    archive_path = root / "knowledge-archive.md"

    # ── ① reviewer ─────────────────────────────────────────────
    knowledge_text = (
        knowledge_path.read_text(encoding="utf-8")
        if knowledge_path.exists() else ""
    )
    review_input = (
        f"objective: {study.objective}\n"
        f"metric_targets: {json.dumps(study.metric_targets, ensure_ascii=False)}\n"
        f"round: {round_num}\n"
        f"verdict: {verdict}\n"
        f"hypothesis: {hypothesis}\n"
        f"manifest: {json.dumps(manifest, ensure_ascii=False, default=str)[:4000]}\n"
        f"last_review: {json.dumps(state.last_review, ensure_ascii=False, default=str)}\n"
        f"continuous_deviation: {state.continuous_deviation}\n"
        f"todos:\n{todos_path.read_text(encoding='utf-8') if todos_path.exists() else ''}\n"
        f"knowledge (recent):\n{knowledge_text[-3000:]}\n"
    )
    use_real = study.behavior is None and should_use_real_llm()
    raw_review = ""
    try:
        if use_real:
            raw_review = run_agent_via_llm(
                role="study_reviewer",
                workspace_path=path,
                strategy_name=study.strategy_name,
                task=review_input,
                max_iterations=3,
                loop_strategy=runner._loop_strategy,
            )
        else:
            raw_review = json.dumps({
                "deviation": "low", "deviation_reason": "stub",
                "info_gap": False, "topics": [],
                "todo_updates": [], "next_focus": "",
            })
    except Exception as exc:
        logger.warning("study_reviewer failed: %s", exc)
        state.review_fail_count += 1
        ss.save(path, sid, state)
        if state.review_fail_count >= 2:
            return ShutdownReason.REVIEW_FAILED
        return None

    review = rl.normalize_review(rl.parse_review_output(raw_review))
    if not raw_review.strip() or not review.get("next_focus"):
        state.review_fail_count += 1
        ss.save(path, sid, state)
        if state.review_fail_count >= 2:
            return ShutdownReason.REVIEW_FAILED
        return None
    state.review_fail_count = 0

    # ── ② information collection ───────────────────────────────
    if rl.should_collect(
        info_gap=review["info_gap"],
        round_num=round_num,
        last_collect_round=state.last_collect_round,
        collect_interval=SR_STUDY_COLLECT_INTERVAL,
    ):
        topics = review["topics"] or [study.objective[:80]]
        runner._collect_knowledge(topics)

    # ── ③ todos application ────────────────────────────────────
    applied = rl.apply_todos(
        todos_path, review["todo_updates"], study.objective,
    )
    if applied:
        runner._emit(session, "study_todos_updated", {
            "study_id": sid, "updates": review["todo_updates"],
        })

    # ── ④ deviation state + stop guard ─────────────────────────
    if review["deviation"] == "high":
        state.continuous_deviation += 1
    else:
        state.continuous_deviation = 0
    state.last_review = {"round": round_num, **review}
    ss.save(path, sid, state)
    runner._emit(session, "study_review", {
        "study_id": sid, "round": round_num,
        "deviation": review["deviation"], "info_gap": review["info_gap"],
    })

    # ── ⑤ manifest phase 2: review overlay + DB mirror ─────────
    manifest = rm.overlay_review(manifest, review)
    rm.save_manifest(manifest, path, sid, round_num)
    try:
        runner.study_store.update_round(sid, round_num, review)
    except Exception as exc:
        logger.warning("update_round failed (mirror): %s", exc)

    # knowledge compaction
    try:
        compacted = rl.maybe_compact(knowledge_path, archive_path=archive_path)
        if compacted:
            runner._emit(session, "study_knowledge_compacted", {
                "study_id": sid, **compacted,
            })
    except Exception as exc:
        logger.warning("knowledge compaction failed: %s", exc)

    # ── ⑥ stop guard ──────────────────────────────────────────
    if state.continuous_deviation >= SR_STUDY_MAX_DEVIATION:
        return ShutdownReason.REPEATED_DEVIATION
    return None
