"""research preset（B1-b）—— phases 引擎分支的 GraphEngine 阶段图形态。

设计：docs/stage-b1b-research-preset-design-20261027.md。

要点：
- **直译而非重写**：novelty 门 / 回测前存证 / parse 重试执行直接 import
  phase_engine 的同名函数，阶段序、参数、emit 时点与 _run_phases_branch
  逐行对照——行为等价由构造保证。
- abort（novelty 拒绝）是正常业务终态：数据标记 + 条件边 → END，
  不用 GraphInterrupt；parse 重试留在节点内部（同阶段重入，非阶段间跳转）。
- 域事件（study_phase）仍走 runner._emit，与旧路径同源同序；引擎的
  node.started/completed 默认不接（sink=None），避免双重事件。
- 落位在 study 域（非 runtimes）：preset 闭包需要 BranchContext，
  依赖方向必须是 域 → 平台（GraphEngine），绝不反向。
"""
from __future__ import annotations

from typing import Any, Callable

from strategy_research.core.runtimes import END, GraphEngine

from .phase_engine import (
    BranchContext,
    RoundOutcome,
    _capture_round_claims,
    _novelty_gate,
    _run_execution_with_parse_retry,
)

EventSink = Callable[[str, dict], None]


def build_research_engine(
    ctx: BranchContext, *, sink: EventSink | None = None
) -> GraphEngine:
    """构造研究轮次阶段图：researcher → novelty →(条件) claims → execution → evaluation。"""
    engine = GraphEngine(sink=sink)
    engine.add_node("researcher", _researcher_node(ctx))
    engine.add_node("novelty", _novelty_node(ctx))
    engine.add_node("claims", _claims_node(ctx))
    engine.add_node("execution", _execution_node(ctx))
    engine.add_node("evaluation", _evaluation_node(ctx))
    engine.add_edge("researcher", "novelty")
    engine.add_conditional_edge(
        "novelty", _novelty_router, {"proceed": "claims", "dup": END}
    )
    engine.add_edge("claims", "execution")
    engine.add_edge("execution", "evaluation")
    engine.add_edge("evaluation", END)
    return engine


def run_phases_via_graph(
    ctx: BranchContext, *, sink: EventSink | None = None
) -> RoundOutcome | dict:
    """图形态的 phases 分支：输出契约与 `_run_phases_branch` 完全一致。

    返回 RoundOutcome（正常路径）或 abort dict（novelty 拒绝，
    与旧分支同形：{"round", "run_name", "aborted", "reason"}）。
    """
    engine = build_research_engine(ctx, sink=sink)
    state = engine.run({})

    if state.get("novelty_ok") is False:
        return {
            "round": ctx.round_num,
            "run_name": ctx.run_name,
            "aborted": True,
            "reason": "novelty_rejected",
        }

    researcher_output = state.get("researcher_output") or {}
    agent_outputs = {
        "researcher": researcher_output,
        **(state.get("exec_agent_outputs") or {}),
        **(state.get("eval_agent_outputs") or {}),
    }
    return RoundOutcome(
        metrics=state["metrics"],
        verdict=state["verdict"],
        eval_result=state["eval_result"],
        agent_outputs=agent_outputs,
        strategist_output=state["strategist_output"],
        hypothesis=state.get("hypothesis", ""),
        predicted_affected=state.get("predicted_affected", []),
        captured_predictions=state.get("captured_predictions", {}),
        backtest_error=state.get("backtest_error"),
    )


# ── 节点工厂（闭包持 ctx；函数体延迟 import，与旧分支同源）──────


def _researcher_node(ctx: BranchContext) -> Callable[[dict], dict]:
    runner, study, session = ctx.runner, ctx.study, ctx.session

    def node(state: dict) -> dict:
        from strategy_research.core.autoresearch import run_researcher_phase
        from .runner import SR_AGENT_MAX_ITER

        runner._emit(session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "researcher", "status": "started",
        })
        result = run_researcher_phase(
            ctx.path, ctx.strategy, ctx.current_state, ctx.run_dir,
            session_id=session, run_name=ctx.run_name,
            behavior=study.behavior, max_retries=3,
            max_iterations=SR_AGENT_MAX_ITER,
            directives=ctx.directive_text,
            lazy_detection_interval=study.lazy_detection_interval,
            keep_recent=study.keep_recent, round_num=ctx.round_num,
            runs_dir=ctx.runs_dir,
            loop_strategy=runner._loop_strategy,
        )
        runner._emit(session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "researcher", "status": "done",
        })
        researcher_output = result["researcher_output"]
        if not isinstance(researcher_output, dict):
            researcher_output = {}
        return {**state, "researcher_output": researcher_output}

    return node


def _novelty_node(ctx: BranchContext) -> Callable[[dict], dict]:
    runner = ctx.runner

    def node(state: dict) -> dict:
        researcher_output = state.get("researcher_output") or {}
        hypothesis = researcher_output.get("hypothesis", "")
        predicted_affected = researcher_output.get("predicted_affected") or [
            t["name"] for t in ctx.metric_targets
        ]
        ok = _novelty_gate(runner, ctx.round_num, hypothesis, predicted_affected)
        return {
            **state,
            "novelty_ok": ok,
            "hypothesis": hypothesis,
            "predicted_affected": predicted_affected,
        }

    return node


def _novelty_router(state: dict) -> str:
    return "proceed" if state.get("novelty_ok") else "dup"


def _claims_node(ctx: BranchContext) -> Callable[[dict], dict]:
    runner = ctx.runner

    def node(state: dict) -> dict:
        predictions = _capture_round_claims(
            runner, ctx.round_num, state.get("researcher_output"),
            state.get("predicted_affected", []), ctx.metric_targets,
        )
        return {**state, "captured_predictions": predictions}

    return node


def _execution_node(ctx: BranchContext) -> Callable[[dict], dict]:
    runner, study, session = ctx.runner, ctx.study, ctx.session

    def node(state: dict) -> dict:
        runner._emit(session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "execution", "status": "started",
        })
        exec_result = _run_execution_with_parse_retry(
            runner, ctx.path, ctx.strategy, ctx.current_state,
            state.get("researcher_output") or {}, ctx.run_dir,
            session=session, run_name=ctx.run_name,
            results_tsv=ctx.results_tsv, round_num=ctx.round_num,
        )
        runner._emit(session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "execution", "status": "done",
        })
        exec_agent_outputs = {
            k: exec_result.get(k) for k in (
                "data_quality_output", "factor_analyst_output",
                "strategist_output", "portfolio_construction_output",
            )
        }
        return {
            **state,
            "metrics": exec_result["metrics"],
            "strategist_output": exec_result["strategist_output"],
            "backtest_result": exec_result["backtest_result"],
            "backtest_error": exec_result.get("backtest_error"),
            "exec_agent_outputs": exec_agent_outputs,
        }

    return node


def _evaluation_node(ctx: BranchContext) -> Callable[[dict], dict]:
    runner, study = ctx.runner, ctx.study

    def node(state: dict) -> dict:
        from strategy_research.core.autoresearch import run_evaluation_phase
        from .runner import SR_AGENT_MAX_ITER

        runner._emit(ctx.session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "evaluation", "status": "started",
        })
        eval_result = run_evaluation_phase(
            ctx.path, ctx.strategy, state["backtest_result"],
            state["metrics"], ctx.run_dir,
            behavior=study.behavior, max_retries=3,
            max_iterations=SR_AGENT_MAX_ITER,
            loop_strategy=runner._loop_strategy,
        )
        runner._emit(ctx.session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "evaluation", "status": "done",
        })
        eval_agent_outputs = {
            k: eval_result.get(k) for k in (
                "risk_controller_output", "attribution_analyst_output",
                "anti_overfit_analyst_output", "backtest_diagnostics_output",
            )
        }
        return {
            **state,
            "verdict": eval_result["verdict"],
            "eval_result": eval_result,
            "eval_agent_outputs": eval_agent_outputs,
        }

    return node


__all__: list[str] = []  # 域内部件：不进包导出面，经 phase_engine 显式接线
