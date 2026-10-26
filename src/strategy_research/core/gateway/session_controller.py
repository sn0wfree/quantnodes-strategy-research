"""SessionController（阶段 1a 最小版）—— 准入 + 签发身份 + 驱动引擎。

P-4 身份：turn_id = {session_id}#{n}（per-session 单调）；
run_id = {turn_id}:r0（turn:run=1:N，1a 每 turn 一次运行）。

生命周期事件：turn.admitted → run.started → [节点事件] → run.settled。
失败路径：异常捕获后 run.settled{ok:False} + run.failed 事件——引擎崩溃
不打断控制器，队列里下一个 turn 照常。

驱动方式：run_pending() 清空 EntryPoint 所有 mailbox（E1 由测试/脚本驱动；
A2 换成后台消费者常驻）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from strategy_research.core.runtimes.graph_engine import GraphEngine
from strategy_research.core.orchestration import EntryPoint, WorkRequest

EventSink = Callable[[str, dict], None]


@dataclass(frozen=True)
class RunResult:
    turn_id: str
    run_id: str
    ok: bool
    answer: str = ""
    error: str | None = None


class SessionController:
    def __init__(
        self,
        entry_point: EntryPoint,
        *,
        client: Any,
        system: str | None = None,
        sink: EventSink | None = None,
    ) -> None:
        self._ep = entry_point
        self._client = client
        self._system = system
        self._sink = sink or (lambda kind, data: None)
        self._turn_counters: dict[str, int] = {}  # session_id -> 已发 turn 数

    # ── 驱动 ─────────────────────────────────────────────

    def run_pending(self) -> list[RunResult]:
        """清空所有 mailbox：逐 turn 准入 → 执行 → settle。"""
        results: list[RunResult] = []
        for session_key, mailbox in list(self._ep.iter_mailboxes()):
            while True:
                request = mailbox.poll_due()
                if request is None:
                    break
                results.append(self._execute(request))
        return results

    # ── 单个 turn ────────────────────────────────────────

    def _execute(self, request: WorkRequest) -> RunResult:
        session_id = self._ep.session_id_for(request.session_key) or ""
        self._turn_counters[session_id] = self._turn_counters.get(session_id, 0) + 1
        turn_id = f"{session_id}#{self._turn_counters[session_id]}"
        run_id = f"{turn_id}:r0"

        self._sink("turn.admitted", {"turn_id": turn_id, "session_id": session_id})
        self._sink("run.started", {"run_id": run_id, "kind": request.kind.value})

        engine = self._build_engine()
        state = {
            "messages": [{"role": "user", "content": _payload_text(request)}],
            "session_id": session_id,
            "turn_id": turn_id,
            "run_id": run_id,
        }
        try:
            final = engine.run(state)
            answer = str(final.get("answer", ""))
        except Exception as exc:  # noqa: BLE001 —— 引擎崩溃不拖垮控制器
            self._sink("run.failed", {"run_id": run_id, "error": str(exc)})
            self._sink("run.settled", {"run_id": run_id, "ok": False})
            return RunResult(turn_id, run_id, ok=False, error=str(exc))

        self._sink(
            "run.settled",
            {"run_id": run_id, "ok": True, "answer_chars": len(answer)},
        )
        return RunResult(turn_id, run_id, ok=True, answer=answer)

    def _build_engine(self) -> GraphEngine:
        from strategy_research.core.runtimes.nodes.llm import make_llm_node

        engine = GraphEngine(sink=self._sink)
        engine.add_node("llm", make_llm_node(self._client, system=self._system))
        return engine


def _payload_text(request: WorkRequest) -> str:
    """1a 约定：payload["text"] 即用户输入（chat 类）。"""
    text = request.payload.get("text")
    return text if isinstance(text, str) else ""
