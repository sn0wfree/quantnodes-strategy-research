"""阶段 4 门禁 —— 依赖方向 / 不变量 / 门退役契约。

这些是**结构性**测试：不验行为，验架构约束不被悄悄破坏。
失败信息指向具体文件与 import 行，便于定位。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "strategy_research"
PLATFORM_PKGS = ("gateway", "orchestration", "runtimes", "storage")


def _iter_py(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _imports(path: Path) -> list[tuple[str, int]]:
    """Return [(dotted module, lineno)] for every import in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, node.lineno))
    return found


# ── 依赖方向：平台 → 域 单向 ────────────────────────────


def test_platform_never_imports_study_domain():
    """core/{gateway,orchestration,runtimes,storage} 不得 import core/study。

    research preset 作为域件住在 core/study/graph_preset.py 并 import
    GraphEngine——方向是 域→平台。反过来（平台 import 域）会把研究域
    变成平台的隐含依赖，破坏「⑤ 研究域独立成层」。
    """
    violations: list[str] = []
    for pkg in PLATFORM_PKGS:
        for path in _iter_py(SRC / "core" / pkg):
            for module, lineno in _imports(path):
                if module.startswith("strategy_research.core.study") or \
                        module.startswith("core.study"):
                    violations.append(f"{path.relative_to(SRC)}:{lineno} -> {module}")
    assert not violations, "平台层反向依赖研究域:\n" + "\n".join(violations)


def test_context_hub_never_imports_producers():
    """core/context 不得 import 任何生产方域（agent/goal/study）。

    ContextHub 三条硬约束之一（docs/context-hub-discussion.md §3）：
    「生产方注册进中枢，中枢从不 import 生产方」——否则它就是下一个
    上帝对象。生产方适配器住在 core/agent/context_providers.py，
    方向是 域→平台。
    """
    violations: list[str] = []
    for path in _iter_py(SRC / "core" / "context"):
        for module, lineno in _imports(path):
            if any(
                module.startswith(f"strategy_research.core.{producer}")
                or module.startswith(f"core.{producer}")
                for producer in ("agent", "goal", "study")
            ):
                violations.append(f"{path.relative_to(SRC)}:{lineno} -> {module}")
    assert not violations, "ContextHub 反向依赖生产方:\n" + "\n".join(violations)


def test_platform_never_imports_fastapi():
    """core/gateway 契约：零 FastAPI import（挂载式宿主的前提）。

    见 gateway/__init__.py 落位规则与 v6 宿主决策。
    """
    violations: list[str] = []
    for path in _iter_py(SRC / "core" / "gateway"):
        for module, lineno in _imports(path):
            if module.startswith(("fastapi", "starlette")):
                violations.append(f"{path.relative_to(SRC)}:{lineno} -> {module}")
    assert not violations, "gateway 依赖了 FastAPI:\n" + "\n".join(violations)


# ── 不变量：可执行登记表全绿 ────────────────────────────


def test_invariants_verify_all_passes():
    from strategy_research.core.gateway.invariants import verify_all

    verify_all()


def test_invariant_registry_covers_documented_set():
    """INV-1/3/5/8/9 必须在登记表里（防悄悄删条目）。"""
    from strategy_research.core.gateway.invariants import invariants

    ids = {inv.id.split(":")[0].strip() for inv in invariants()}
    assert {"INV-1", "INV-3", "INV-5", "INV-8", "INV-9"} <= ids


# ── 门退役契约（阶段 4）──────────────────────────────


def test_research_graph_gate_is_retired():
    """门已退役：恒 True，环境变量无效。"""
    import os

    from strategy_research.core.gateway import research_graph_enabled

    assert research_graph_enabled() is True
    for value in ("0", "1", "false"):
        os.environ["QNODES_RESEARCH_GRAPH"] = value
        try:
            assert research_graph_enabled() is True
        finally:
            os.environ.pop("QNODES_RESEARCH_GRAPH", None)


def test_phase_engine_has_no_gate_import():
    """phase_engine 不再 import research_graph_enabled（门接线已移除）。"""
    path = SRC / "core" / "study" / "phase_engine.py"
    source = path.read_text(encoding="utf-8")
    # 允许文档字符串提及，但不得有实际调用
    assert "research_graph_enabled()" not in source


# ── 消费归属：kind 过滤不得丢失工单 ────────────────────


def test_mailbox_kind_filter_never_drops_work():
    from strategy_research.core.gateway import scheduler_request
    from strategy_research.core.orchestration import EntryPoint, WorkKind

    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(scheduler_request("s", {"study_id": "x"}))
    mail = ep.mailbox_for("automation:s")

    # 反复用错误的 kind 过滤，工单必须一直在
    for _ in range(5):
        assert mail.poll_due({WorkKind.CHAT}) is None
        assert mail.peek_depth() == 1
    assert mail.poll_due({WorkKind.AUTOMATION}) is not None


def test_chat_kind_filter_never_drops_chat_work():
    from strategy_research.core.gateway import api_chat_request
    from strategy_research.core.orchestration import EntryPoint, WorkKind

    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(api_chat_request("s", "hi"))
    mail = ep.mailbox_for("chat:s")

    assert mail.poll_due({WorkKind.AUTOMATION}) is None
    assert mail.peek_depth() == 1
    assert mail.poll_due({WorkKind.CHAT}) is not None


# ── 用户主目录沙箱（阶段 4 测试隔离）───────────────────


def test_user_home_is_sandboxed_during_tests():
    """整个测试进程的 home 解析必须落在沙箱。

    背景：曾有测试经 Windows Path.home()（读 USERPROFILE 而非 HOME）长期
    覆写宿主 ~/.quantnodes/jwt_secret。conftest 顶部重定向后，任何测试里
    的 Path.home()/expanduser("~") 都不得再指向真实主目录。
    """
    import os
    from pathlib import Path

    import _home_sandbox

    assert _home_sandbox.sandboxed(), "conftest 未重定向 USERPROFILE"
    assert Path.home() == _home_sandbox.SANDBOX_HOME
    assert os.path.expanduser("~") == str(_home_sandbox.SANDBOX_HOME)
    # 模块级常量（import 时求值）也在沙箱里
    from strategy_research.core.llm import quantnodes_bridge

    assert str(_home_sandbox.SANDBOX_HOME) in str(quantnodes_bridge.CONFIG_PATH)


def test_sandbox_home_is_outside_real_home():
    """沙箱必须与真实主目录物理隔离（不能是它的子目录）。"""
    import _home_sandbox

    sandbox = _home_sandbox.SANDBOX_HOME.resolve()
    real = _home_sandbox.REAL_HOME.resolve()
    assert real not in sandbox.parents
