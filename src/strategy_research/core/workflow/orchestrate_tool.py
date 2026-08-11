"""SubmitDagStepTool — the only tool exposed to DAG-orchestrator sessions.

The LLM submits the full modified DAG (one incremental step per call);
the tool validates it with the exact same ``WorkflowDefinition.validate``
used by the definitions save API, so anything that would 422 at save time
is caught here and returned to the LLM for self-correction inside the
agent loop.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent.tools import BaseTool, ToolContext

MAX_NODES = 50


def _validate_dag(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    """Validate a DAG payload; returns [] when valid."""
    from .definition import WorkflowDefinition

    definition = WorkflowDefinition.from_dict(
        {"name": "orchestrate", "nodes": nodes, "edges": edges},
        source="orchestrator",
    )
    errors = definition.validate()
    if len(nodes) > MAX_NODES:
        errors.append(f"node count {len(nodes)} exceeds limit {MAX_NODES}")
    return errors


class SubmitDagStepTool(BaseTool):
    """提交 DAG 修改（一次性提交修改后的完整 DAG，校验通过即应用）。

    # ── 工具说明书 ──────────────────────────────
    # ## 用途
    # 编排会话专用：LLM 每轮把「修改后的完整 DAG」提交给此工具。工具做与
    # 保存定义一致的服务端校验（6 种节点类型、planner/evaluator/approval
    # 各最多 1 个、依赖无环、id 引用完整、节点数上限）。
    #
    # ## 行为
    # - 校验通过 → {"applied": true, ...}（前端据此应用画布）
    # - 校验失败 → {"applied": false, "errors": [...]}，错误逐条可读，
    #   供 LLM 修正后重新提交（agent loop 内自动回传）
    #
    # ## 格式
    # {"dag": {"nodes": [{"id","type","label","config"}], "edges": [{"source","target"}]}}
    # ─────────────────────────────────────────────
    """

    name = "submit_dag_step"
    description = (
        "提交修改后的完整 DAG（含全部节点与连线）。每轮只做一处增量修改："
        "新增/删除/修改一个节点或一条连线。校验通过返回 applied:true；"
        "失败返回 applied:false 与 errors 列表，请逐条修正后重新提交。"
    )
    repeatable = True
    category = "编排"

    def execute(self, ctx: ToolContext, dag: dict[str, Any]) -> str:
        try:
            nodes = list(dag.get("nodes") or [])
            edges = list(dag.get("edges") or [])
        except (AttributeError, TypeError) as exc:
            return json.dumps(
                {"applied": False, "errors": [f"dag 参数不是合法 JSON 对象: {exc}"]},
                ensure_ascii=False,
            )

        if not isinstance(nodes, list) or not isinstance(edges, list):
            return json.dumps(
                {"applied": False, "errors": ["dag.nodes 与 dag.edges 必须为数组"]},
                ensure_ascii=False,
            )

        errors = _validate_dag(nodes, edges)
        if errors:
            return json.dumps(
                {"applied": False, "errors": errors},
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "applied": True,
                "nodes": len(nodes),
                "edges": len(edges),
                "diff": f"当前 DAG: {len(nodes)} 节点 / {len(edges)} 连线",
            },
            ensure_ascii=False,
        )
