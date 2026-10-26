"""A5 不变量层 —— 系统底线的集中收口（Tier 0，任何代码不得绕过）。

设计（A5 = DSH §9.7 模式）：
- 每条不变量 = 一个**结构化检查点**（verify 函数）+ 元数据（id/描述/执行时机）。
- `verify_all()` 在 Gateway 启动时调用：任何一条失败 → **拒启**。
- CI（0.5）再对清单做静态断言（import guard 等），双层防护。

不变量清单（2026-10-26，随刀号增长）：
  INV-1 workers=1：多 worker = 多 Gateway = mailbox 分裂（ConfigHub 结构钉死）
  INV-2 事件只追加：event_log 无 UPDATE/DELETE 路径（PlatformStore 承诺）
  INV-3 唯一开 turn 路径 = admission（mailbox 消费只在控制器）
  INV-4 产物落库必过 fence（写路径出示 FenceToken 且 validate 通过）
  INV-5 同一 turn 至多一个活跃 run（fence claim 即超越）
  INV-6 拒绝/降级/过期必发事件（无静默）
  INV-7 控制面无模型调用、无重计算（结构性——CI import guard + 快同步断言）
  INV-8 配置 fail-fast（ConfigError 拒启，不猜默认）

本模块 v1 提供：清单注册表 + 可执行的结构检查（能程序化验证的部分）
+ verify_all()。**检查点本体在各自模块**（ConfigHub/fence/EventStore）——
这里收录它们的验证入口，而非复制逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Invariant:
    id: str                     # INV-n
    description: str
    verify: Callable[[], bool]  # True=满足；False=违反（拒启）
    enforced_since: str         # 首次强制执行的刀号


class InvariantViolation(Exception):
    """启动时不变量被违反 → 拒启。"""


def _inv_workers_pinned() -> bool:
    """INV-1：ConfigHub 的 workers 字段不可配置且恒为 1。"""
    from ..orchestration.config_hub import GatewayConfig

    try:
        GatewayConfig(workers=4)
        return False  # 覆盖成功 = 钉死失效
    except Exception:  # noqa: BLE001 —— 预期 TypeError/ValueError
        return True


def _inv_fence_supersedes() -> bool:
    """INV-5：fence 认领即超越——旧 run 提交必被拦。"""
    from .fencing import SessionFence

    fence = SessionFence()
    old = fence.claim("inv-check", "old")
    fence.claim("inv-check", "new")
    ok = not fence.validate(old).ok and fence.validate(
        fence.claim("inv-check", "new")
    ).ok
    fence.release(fence.claim("inv-check", "cleanup"))
    return ok


def _inv_config_fail_fast() -> bool:
    """INV-8：非法配置必须抛 ConfigError（不猜默认）。"""
    from ..orchestration.config_hub import ConfigError, load_platform_config

    try:
        load_platform_config(env={"QNODES_MAILBOX__CAP": "not-a-number"})
        return False
    except ConfigError:
        return True


def _inv_entry_reject_no_side_effects() -> bool:
    """INV-3 前置：入口拒绝无副作用、collect 留位拒绝。"""
    from ..orchestration.contracts import QueueMode, SourceKind, WorkKind, WorkRequest
    from ..orchestration.entry_point import EntryPoint

    ep = EntryPoint(sink=lambda k, d: None)
    receipt = ep.submit(
        WorkRequest(
            kind=WorkKind.CHAT,
            source=SourceKind.API_CHAT,
            session_key="chat:inv",
            payload={"text": "x"},
            queue_mode=QueueMode.COLLECT,
        )
    )
    return not receipt.accepted and ep.session_id_for("chat:inv") is None


_REGISTRY: tuple[Invariant, ...] = (
    Invariant("INV-1", "workers 恒为 1（多 worker = 多 Gateway）", _inv_workers_pinned, "E1"),
    Invariant("INV-3", "唯一开 turn 路径 = admission；入口拒绝无副作用", _inv_entry_reject_no_side_effects, "E1"),
    Invariant("INV-5", "fence 认领即超越：同 turn 至多一活跃 run", _inv_fence_supersedes, "A3"),
    Invariant("INV-8", "配置 fail-fast：非法配置拒启", _inv_config_fail_fast, "E1"),
)


def invariants() -> tuple[Invariant, ...]:
    return _REGISTRY


def verify_all(*, raise_on_violation: bool = True) -> tuple[bool, list[str]]:
    """启动时验证全部不变量。任一失败 → InvariantViolation（拒启）。"""
    violations: list[str] = []
    for inv in _REGISTRY:
        try:
            ok = inv.verify()
        except Exception as exc:  # noqa: BLE001 —— 检查器自身异常也算违反
            ok = False
            violations.append(f"{inv.id}: 检查器异常 {exc!r}")
            continue
        if not ok:
            violations.append(f"{inv.id}: {inv.description}")
    if violations and raise_on_violation:
        raise InvariantViolation("; ".join(violations))
    return (not violations), violations
