"""11 BaseTool tools for agent code interaction.

All tools accept `workspace` (Path-like) via kwargs injection by AgentLoop.
Each tool returns a JSON string (success or error envelope).

Tools:
    ReadFileTool       - read files inside workspace
    WriteFileTool      - write files (sandbox + AST guard for .py)
    RunBacktestTool    - invoke core.backtest.run_backtest_from_yaml
    ComputeFactorTool  - invoke core.compute_factor.compute_factor
    GitDiffTool        - subprocess wrapper for git diff
    ListHistoryTool    - list runs from results.tsv + runs/ directory
    FactorAnalysisTool - factor IC/IR analysis
    PatternRecognitionTool - detect chart patterns
    ListSkillsTool     - list available methodology skills
    LoadSkillTool      - load full skill content by name
    OptionsPricingTool - Black-Scholes options pricing
"""

from __future__ import annotations

import inspect
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ...backtest import run_backtest_from_yaml
from ...compute_factor import compute_factor, FactorComputeError
from ..sandbox import (
    PathValidationError,
    PathWhitelist,
    validate_python_source,
)
from ..tools import (
    EFFECT_DB,
    EFFECT_FS,
    EFFECT_NET,
    BaseTool,
    ToolContext,
    ToolError,
    ToolRegistry,
)
from .utils import err_actionable, safe_get_param, try_unwrap_list, try_unwrap_dict

logger = logging.getLogger(__name__)


# ── Shared helpers ───────────────────────────────────────────────────


def _workspace_from_kwargs(kwargs: dict[str, Any]) -> Path:
    """Extract and normalize workspace path from kwargs."""
    ws = kwargs.get("workspace")
    if ws is None:
        raise ValueError("missing required kwarg 'workspace'")
    if isinstance(ws, str):
        ws = Path(ws)
    if not isinstance(ws, Path):
        raise ValueError(f"workspace must be Path or str, got {type(ws).__name__}")
    return ws.resolve()


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def _err(message: str, **extra: Any) -> str:
    return json.dumps(
        {"status": "error", "error": str(message), **extra},
        ensure_ascii=False,
    )


def _workspace_error(exc: ValueError, *, tool: str) -> str:
    """Convert _workspace_from_kwargs ValueError to actionable error."""
    return err_actionable(
        str(exc),
        expected="absolute path to workspace root, e.g. '/home/user/qn-research'",
        fix="pass workspace='/path/to/your/workspace' (the project root containing strategies/, data.duckdb)",
        tool=tool,
    )


# ── 1. ReadFileTool ─────────────────────────────────────────────────


class ReadFileTool(BaseTool):
    """读取工作区文件内容（只读，可限制行数）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext; schema 自动派生)
    #
    # ## 用途
    # 读取工作区内文件内容, 支持 limit/offset 分片。路径相对 workspace,
    # 必须位于允许的读取根目录 (strategies/templates/memory/logs/data/docs/.)。
    #
    # ## 参数
    # - path: 相对 workspace 的文件路径 (必填)
    # - limit: 返回的最大行数 (可选)
    # - offset: 起始行偏移, 0 起 (可选)
    #
    # ## 示例
    # {"path": "strategies/momentum_20d/strategy.py"}
    #
    # ## 边界
    # 只读工具; 白名单外路径/绝对路径/.. 会被拒绝; 二进制/非 UTF-8 文件报错。
    #
    # ## 错误处理范式
    # - 缺 path → error + expected 示例
    # - 白名单外 → error + fix 提示允许根目录
    # - 文件不存在/是目录 → error + fix 用 list_files 确认
    # - 非 UTF-8 → 提示用 read_document 或跳过
    # - 所有失败均可安全重试
    #
    # ## 相关工具
    # list_files: 浏览目录; write_file: 写入
    # ─────────────────────────────────────────────
    """

    name = "read_file"
    description = (
        "读取工作区内文件内容 (行数限制可选); 路径相对 workspace, "
        "限允许根目录 (strategies/templates/memory/logs/data/docs/.)。"
    )
    repeatable = True
    category = "文件"

    def execute(
        self,
        ctx: ToolContext,
        path: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="read_file",
            )
        workspace = ctx.workspace

        if not isinstance(path, str) or not path:
            return err_actionable(
                "missing or invalid 'path'",
                received=path,
                expected="non-empty string path relative to workspace, e.g. 'strategies/momentum_20d/strategy.py'",
                fix="pass path='strategies/<name>/strategy.py' or 'templates/strategy.py'",
                tool="read_file",
            )

        wl = PathWhitelist(workspace=workspace)
        try:
            resolved = wl.resolve_read(path)
        except PathValidationError as exc:
            return err_actionable(
                str(exc),
                received=path,
                expected="path under an allowed read root (strategies/templates/memory/logs/data/docs/)",
                fix="use a path under strategies/, templates/, memory/, logs/, data/, or docs/",
                tool="read_file",
            )

        if not resolved.exists():
            return err_actionable(
                f"file not found: {path}",
                received=path,
                fix="verify the path exists with list_files(workspace=..., path='<dir>')",
                tool="read_file",
                extra={"resolved_path": str(resolved)},
            )
        if not resolved.is_file():
            return err_actionable(
                f"not a regular file: {path}",
                received=path,
                fix="use list_files to list a directory, read_file on a file",
                tool="read_file",
                extra={"resolved_path": str(resolved)},
            )

        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return err_actionable(
                f"file is not valid UTF-8: {path}",
                fix="file may be binary; use read_document for PDF, or skip this file",
                tool="read_file",
            )
        except OSError as exc:
            return err_actionable(
                f"read failed: {exc}",
                fix="check file permissions",
                tool="read_file",
            )

        all_lines = content.splitlines()
        if offset:
            all_lines = all_lines[offset:]
        if limit is not None:
            all_lines = all_lines[: int(limit)]
        output = "\n".join(all_lines)

        return _ok({
            "path": str(resolved),
            "content": output,
            "total_lines": len(content.splitlines()),
            "returned_lines": len(all_lines),
        })


# ── 1b. ListFilesTool ─────────────────────────────────────────────


class ListFilesTool(BaseTool):
    """列出工作区目录内容（文件/子目录，支持 glob）。 

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 浏览工作区目录结构: 文件与子目录清单 (含大小)。读文件前先用它探索。
    #
    # ## 参数
    # - path: 目录路径, 相对 workspace (默认 '.')
    # - pattern: glob 过滤 (可选, 如 '*.py' / 'strategies/*')
    #
    # ## 示例
    # {"path": "strategies"}
    #
    # ## 边界
    # 只读工具; 仅限 workspace 内目录; 文件路径会报错 (用 read_file)。
    #
    # ## 错误处理范式
    # - 路径不存在 → error + fix 提示顶层结构
    # - 目标是文件 → error + fix 用 read_file
    # - 均可安全重试
    #
    # ## 相关工具
    # read_file: 读文件内容; write_file: 写入
    # ─────────────────────────────────────────────
    """

    name = "list_files"
    description = (
        "列出工作区目录内容 (文件/子目录, 含大小); path 相对 workspace, "
        "可用 glob pattern 过滤。"
    )
    repeatable = True
    category = "文件"

    def execute(
        self,
        ctx: ToolContext,
        path: str = ".",
        pattern: str | None = None,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="list_files",
            )
        workspace = ctx.workspace

        rel_path = path or "."

        target = (workspace / rel_path).resolve()
        if not target.exists():
            return err_actionable(
                f"path not found: {rel_path}",
                received=rel_path,
                expected="directory path relative to workspace, e.g. 'strategies' or '.' for root",
                fix="verify the path exists; use list_files(path='.') to see top-level dirs",
                tool="list_files",
            )
        if not target.is_dir():
            return err_actionable(
                f"not a directory: {rel_path}",
                received=rel_path,
                fix="use read_file for files, list_files for directories",
                tool="list_files",
            )

        entries = []
        if pattern:
            for p in sorted(target.glob(pattern)):
                entries.append({
                    "name": p.name,
                    "type": "dir" if p.is_dir() else "file",
                    "size": p.stat().st_size if p.is_file() else None,
                })
        else:
            for p in sorted(target.iterdir()):
                entries.append({
                    "name": p.name,
                    "type": "dir" if p.is_dir() else "file",
                    "size": p.stat().st_size if p.is_file() else None,
                })
        return _ok({
            "path": str(target),
            "entries": entries,
            "count": len(entries),
        })


# ── 2. WriteFileTool ────────────────────────────────────────────────


class WriteFileTool(BaseTool):
    """写入工作区文件（沙箱路径白名单 + .py AST 安全检查）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext; 副作用改 effects)
    #
    # ## 用途
    # 写入文件内容到工作区。路径限允许写根目录
    # (strategies/templates/memory/logs); .py 文件做 AST 校验,
    # 危险代码 (exec/eval、受限 import、dunder 访问) 会被拒绝。
    #
    # ## 参数
    # - path: 相对 workspace 的文件路径 (必填, 限写白名单)
    # - content: 文件内容 (必填, 字符串)
    #
    # ## 示例
    # {"path": "strategies/momentum_20d/strategy.py", "content": "..."}
    #
    # ## 边界
    # 写工具 (effects=fs); 自动创建父目录; 覆盖已有文件。
    #
    # ## 错误处理范式
    # - 缺 path/content → error + expected 示例
    # - AST 校验失败 → error 含具体危险代码说明
    # - 白名单外 → error + fix 允许根目录
    # - 写入失败 → error + fix 检查权限
    # - 幂等: 重跑覆盖同一路径, 安全
    #
    # ## 相关工具
    # read_file: 读回校验; list_files: 浏览
    # ─────────────────────────────────────────────
    """

    name = "write_file"
    description = (
        "写入文件到工作区 (限 strategies/templates/memory/logs 写白名单); "
        ".py 做 AST 安全检查, 危险代码被拒。"
    )
    repeatable = True
    strict = True  # All params required, no dict-shape → strict-safe
    category = "文件"
    effects = frozenset({EFFECT_FS})

    def execute(self, ctx: ToolContext, path: str, content: str) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="write_file",
            )
        workspace = ctx.workspace

        if not isinstance(path, str) or not path:
            return err_actionable(
                "missing or invalid 'path'",
                received=path,
                expected="non-empty string path, e.g. 'strategies/momentum_20d/strategy.py'",
                fix="pass a non-empty path",
                tool="write_file",
            )
        if not isinstance(content, str):
            return err_actionable(
                "missing or invalid 'content'",
                received=type(content).__name__,
                expected="string content for the file",
                fix="pass content as a string, e.g. content='# strategy parameters\\nPARAMS = {...}'",
                tool="write_file",
            )

        # AST guard for .py files
        if path.endswith(".py"):
            ok, msg = validate_python_source(content)
            if not ok:
                return err_actionable(
                    f"AST validation failed: {msg}",
                    received=content[:200],
                    fix="remove dangerous code (exec/eval, blocked imports, dunder access); see sandbox rules",
                    tool="write_file",
                )

        wl = PathWhitelist(workspace=workspace)
        try:
            resolved = wl.resolve_write(path)
        except PathValidationError as exc:
            return err_actionable(
                str(exc),
                received=path,
                expected="path under an allowed write root (strategies/templates/memory/logs)",
                fix="use a path under strategies/, templates/, memory/, or logs/",
                tool="write_file",
            )

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except OSError as exc:
            return err_actionable(
                f"write failed: {exc}",
                fix="check filesystem permissions and disk space",
                tool="write_file",
            )

        return _ok({
            "path": str(resolved),
            "bytes_written": len(content.encode("utf-8")),
        })


# ── 3. RunBacktestTool ──────────────────────────────────────────────


class RunBacktestTool(BaseTool):
    """从策略配置运行回测并写入 runs/ 与 DuckDB。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.2.0
    # 变更: v1.2.0 数据就绪性门禁 (C1~C6 预检, 不可跑时返回诊断不建 run);
    #       执行拆分为 3 步组合 (config_load → data_gate → engine_run),
    #       错误带 step 标识 (docs/run-backtest-data-gate.md)
    #
    # ## 用途
    # 读取 strategies/<name>/config.yaml 运行回测, 产出新 run 写入
    # runs/<name>/ 与 DuckDB。策略配置就绪且数据已入库后验证表现。
    # 数据未入库时先 get_market_data; 只看历史结果用 list_history。
    #
    # ## 参数
    # - strategy_name: 策略目录名 (必填, strategies/<name>/config.yaml 须存在)
    # - action: 运行标注 (审计用, 默认 'agent')
    # - description: 可选描述
    # - yaml_path: 覆盖默认 config 路径 (相对 workspace)
    #
    # ## 示例
    # {"strategy_name": "momentum_20d"}
    #
    # ## 边界
    # 写工具 (effects: db + fs); 前置: price_data 已有该策略数据;
    # 同策略重复运行产生新 run, 不覆盖旧 run。
    #
    # ## 错误处理范式
    # - 缺 strategy_name → error + expected 示例
    # - 策略目录不存在 → fix 提示 list_files 查看 strategies/
    # - 数据未就绪 (缺资产/窗口不足/密度不足/因子语法错) → 执行前拦截,
    #   error 带 readiness 报告 (step=data_gate), 不创建 run
    # - 运行期因子失败 → 返回 factor_failures 摘要 + 落盘 factor_failures.json
    # - 配置 YAML 非法 → fix 指向 config.yaml 检查
    # - 所有失败均可安全重试 (无部分写入遗留)
    #
    # ## 相关工具
    # get_market_data: 数据前置; check_data: 数据就绪性完整报告;
    # clean_data: 行级清洗; list_history/drawdown_analysis/
    # benchmark_comparison: 结果消费
    # ─────────────────────────────────────────────
    """

    name = "run_backtest"
    description = (
        "从 strategies/<name>/config.yaml 运行回测, 新 run 写入 runs/ 与 DuckDB。"
    )
    repeatable = True
    category = "回测"
    effects = frozenset({EFFECT_DB, EFFECT_FS})

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str,
        action: str = "agent",
        description: str = "",
        yaml_path: str | None = None,
    ) -> str:
        if ctx.workspace is None:
            raise ToolError(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="run_backtest",
            )
        workspace = ctx.workspace

        if not isinstance(strategy_name, str) or not strategy_name:
            raise ToolError(
                "missing or invalid 'strategy_name'",
                received=strategy_name,
                expected="non-empty strategy name, e.g. 'momentum_20d'",
                fix="list strategies with list_files(workspace=..., path='strategies') and pick an existing name",
                tool="run_backtest",
            )
        action = action or "agent"
        description = description or ""

        # ── 步骤 1: 配置加载 ────────────────────────────────────
        cfg_step = ConfigLoadStep()
        out = json.loads(cfg_step.execute(
            ctx=ctx, workspace=workspace, strategy_name=strategy_name,
            yaml_path=yaml_path,
        ))
        if out.get("status") == "error":
            return json.dumps(out, ensure_ascii=False)
        cfg = out["cfg"]
        resolved_yaml = out.get("yaml_path")

        # ── 步骤 2: 数据准备 ────────────────────────────────────
        # config source=auto/auto+duckdb 语义下引擎会在线获取数据 ——
        # 数据准备先执行（写 DB），门禁随后检查的才是「最终将用于回测
        # 的数据」（docs/run-backtest-data-gate.md）。
        prepare_step = DataPrepareStep()
        out = json.loads(prepare_step.execute(
            ctx=ctx, workspace=workspace, cfg=cfg,
        ))
        if out.get("status") == "error":
            return json.dumps(out, ensure_ascii=False)

        # ── 步骤 3: 数据就绪性门禁 (C1~C6, 轻量) ────────────────
        gate_step = DataReadinessStep()
        out = json.loads(gate_step.execute(
            ctx=ctx, workspace=workspace, strategy_name=strategy_name, cfg=cfg,
        ))
        if out.get("status") == "error":
            return json.dumps(out, ensure_ascii=False)
        readiness_summary = out.get("report", {})

        # ── 步骤 4: 引擎执行 + 产物落盘 ─────────────────────────
        engine_step = EngineRunStep()
        out = json.loads(engine_step.execute(
            ctx=ctx, workspace=workspace, strategy_name=strategy_name,
            cfg=cfg, yaml_path=resolved_yaml, action=action,
            description=description,
        ))
        if out.get("status") == "error":
            return json.dumps(out, ensure_ascii=False)

        run_name = out.get("run", "")
        artifacts = {
            "equity_curve": f"runs/{strategy_name}/{run_name}/equity_curve.csv",
            "metrics": f"runs/{strategy_name}/{run_name}/metrics.json",
            "run_card": f"runs/{strategy_name}/{run_name}/run_card.json",
        }
        return json.dumps({
            "status": "ok",
            "run": run_name,
            "strategy": strategy_name,
            "metrics": out.get("metrics", {}),
            "run_status": out.get("status", "success"),
            "factor_failures": out.get("factor_failures", []),
            "warnings": out.get("warnings", []),
            "readiness": readiness_summary,
            # 产物引用（相对 workspace）。净值数据本身不进上下文：
            # 需要展示曲线时用 show_chart(source_file=...) 引用文件，
            # 需要排查时用 read_file 读文件内容。
            "artifacts": artifacts,
        }, ensure_ascii=False, default=str)


# ── RunBacktestTool 组合子步骤 ───────────────────────────────────────
# 每步是 BaseTool 子类（自动获得 tool_errors 标准化），但不注册进
# ToolRegistry（不占 LLM schema）。RunBacktestTool 顺序编排，错误带
# step 标识，精确定位失败环节（docs/run-backtest-data-gate.md）。


class ConfigLoadStep(BaseTool):
    """子步骤: 读取并解析 strategies/<name>/config.yaml。"""

    name = "config_load"

    def execute(
        self,
        ctx: ToolContext,
        workspace,
        strategy_name: str,
        yaml_path: str | None = None,
    ) -> dict:
        from ...config_runner import load_yaml_config

        if yaml_path is not None:
            yaml_path = str(workspace / yaml_path)
        else:
            yaml_path = str(workspace / "strategies" / strategy_name / "config.yaml")

        if not Path(yaml_path).exists():
            raise ToolError(
                f"配置文件不存在: {yaml_path}",
                received=strategy_name,
                expected="strategies/<name>/config.yaml",
                fix="check that the strategy directory exists; use list_files(workspace=..., path='strategies')",
                tool="run_backtest",
                step="config_load",
            )
        try:
            cfg = load_yaml_config(yaml_path)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(
                f"配置 YAML 非法: {exc}",
                received=strategy_name,
                fix="fix syntax in strategies/<name>/config.yaml",
                tool="run_backtest",
                step="config_load",
            ) from exc
        return {"cfg": cfg, "yaml_path": yaml_path}


class DataPrepareStep(BaseTool):
    """子步骤: 数据准备（执行 config_runner.load_data）。

    source=auto/auto+duckdb 语义下引擎会在线获取数据并写入 DuckDB；
    提前到这里执行，门禁（DataReadinessStep）随后检查的才是最终将
    用于回测的数据。幂等（fetch 用 INSERT OR REPLACE）。
    """

    name = "data_prepare"

    def execute(
        self,
        ctx: ToolContext,
        workspace,
        cfg: dict,
    ) -> dict:
        from ...config_runner import load_data

        try:
            df = load_data(cfg, workspace)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(
                f"数据准备失败: {exc}",
                fix="检查网络/数据源可用性，或先用 get_market_data 手动补齐",
                tool="run_backtest",
                step="data_prepare",
            ) from exc
        if df is None or df.empty:
            raise ToolError(
                "数据为空: 在线获取失败且本地无该策略数据",
                fix="get_market_data(codes=[...], strategy_name='<当前策略名>') 手动获取后重试",
                tool="run_backtest",
                step="data_prepare",
            )
        return {"rows": int(len(df)), "cols": list(df.columns)}


class DataReadinessStep(BaseTool):
    """子步骤: 数据就绪性门禁（C1~C6，轻量只读）。不可跑时拦截。"""

    name = "data_gate"

    def execute(
        self,
        ctx: ToolContext,
        workspace,
        strategy_name: str,
        cfg: dict,
    ) -> dict:
        from ...data_readiness import check_data_readiness

        try:
            report = check_data_readiness(workspace, strategy_name, cfg=cfg)
        except Exception as exc:  # noqa: BLE001
            # 检查自身异常不阻塞主流程（降级为 warn 并继续）
            return {"report": {"ok": True, "checks": [
                {"id": "C0", "name": "就绪性检查", "status": "warn",
                 "detail": f"检查不可用: {str(exc)[:150]}",
                 "fix_hint": "继续执行（门禁降级）"},
            ]}}
        if not report.ok:
            fails = [c for c in report.checks if c.status == "fail"]
            # 措辞带后果说明: 门禁拦的是「结果不可信」而非「崩溃」
            details = "; ".join(f"{c.id} {c.detail}" for c in fails[:5])
            raise ToolError(
                f"数据未就绪，共 {len(fails)} 个阻断项（回测结果将不可信，"
                f"已在创建 run 之前拦截）: {details}",
                extra={
                    "readiness": report.to_dict(),
                    "hint": (
                        "按 readiness.checks[*].fix_hint 逐项处理，例如 "
                        "get_market_data(...) 补齐数据后重试；行级问题用 "
                        "check_data(strategy_name=..., include_cleaning=True) "
                        "查看 C7 并 clean_data 清洗"
                    ),
                },
                tool="run_backtest",
                step="data_gate",
            )
        return {"report": report.to_dict()}


class EngineRunStep(BaseTool):
    """子步骤: 引擎执行 + 产物落盘（metrics/factor_failures/equity_curve/DB）。"""

    name = "engine_run"

    def execute(
        self,
        ctx: ToolContext,
        workspace,
        strategy_name: str,
        cfg: dict,
        yaml_path: str | None,
        action: str,
        description: str,
    ) -> dict:
        from ...backtest import run_backtest_from_yaml

        try:
            result = run_backtest_from_yaml(
                workspace_path=workspace,
                strategy_name=strategy_name,
                yaml_path=yaml_path,
                action=action,
                description=description,
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolError(
                f"backtest raised: {exc}",
                received=strategy_name,
                fix="check that strategies/<name>/config.yaml exists and is valid YAML",
                tool="run_backtest",
                step="engine_run",
            ) from exc

        if not result.get("success", False):
            err_msg = result.get("error", "unknown backtest failure")
            extra: dict = {
                "run": result.get("run", ""),
                "metrics": result.get("metrics", {}),
            }
            fix_msg = "check strategies/<name>/config.yaml and runs/<name>/logs for details"
            if (
                "数据为空" in err_msg
                or "empty" in err_msg.lower()
                or "cannot open database" in err_msg.lower()
            ):
                fix_msg = (
                    "data is empty. Workflow: 1) get_market_data("
                    "codes=['600519.SH'], start_date='2023-01-01', "
                    "end_date='2023-12-31', strategy_name='<name>') fetches "
                    "and persists OHLCV to DuckDB in one step; 2) "
                    "run_backtest(strategy_name='<name>') again"
                )
                extra["workflow"] = ["get_market_data", "run_backtest"]
            raise ToolError(
                err_msg,
                received=strategy_name,
                fix=fix_msg,
                tool="run_backtest",
                step="engine_run",
                extra=extra,
            )

        return {
            "run": result.get("run", ""),
            "status": result.get("status", "success"),
            "metrics": result.get("metrics", {}),
            "factor_failures": result.get("factor_failures", []),
            "warnings": result.get("metrics", {}).get("warnings", []),
        }


# ── 4. ComputeFactorTool ────────────────────────────────────────────


class ComputeFactorTool(BaseTool):
    """在工作区价格数据上计算因子表达式（单资产，返回采样）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 在单资产宽表 (close/open/high/low/volume) 上计算因子表达式
    # (如 'ts_mean(close, 20) / ts_mean(close, 60) - 1'), 返回结果采样。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填)
    # - asset: 资产代码 (默认第一个可用资产)
    # - factor_name: 因子名 (可选, 用于展示)
    # - n_samples: 采样数 (默认 5)
    #
    # ## 示例
    # {"factor_code": "ts_return(close, 20)"}
    #
    # ## 边界
    # 只读工具; 读取 workspace DuckDB 的 ohlcv 视图 (price_data);
    # 数据为空会给 workflow 提示。
    #
    # ## 错误处理范式
    # - 缺 factor_code → error + expected 示例
    # - 无 DB/空表 → error + fix: get_market_data → compute_factor
    # - asset 不存在 → error + expected 可用资产列表
    # - 表达式错误 → error + available_columns 与示例表达式
    # - 均可安全重试
    #
    # ## 相关工具
    # get_market_data: 数据前置; factor_analysis/factor_quintile_returns 等: 后续分析
    # ─────────────────────────────────────────────
    """

    name = "compute_factor"
    description = (
        "在单资产价格数据上计算因子表达式 (如 'ts_mean(close, 20) / ts_mean(close, 60) - 1'), "
        "返回结果采样; 数据来自 workspace DuckDB。"
    )
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        asset: str | None = None,
        factor_name: str | None = None,
        n_samples: int = 5,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="compute_factor",
            )
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable(
                "missing or invalid 'factor_code'",
                received=factor_code,
                expected="non-empty factor expression, e.g. 'ts_mean(close, 20) / ts_mean(close, 60) - 1'",
                fix="pass a valid expression; see templates/.skills/factor-research.md for operators",
                tool="compute_factor",
            )
        factor_name = factor_name or ""

        # Load price data from workspace DuckDB
        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:                    # noqa: BLE001
            return err_actionable(
                f"db open failed: {exc}",
                fix="ensure workspace has data.duckdb; run quantnodes-research init or import_data first",
                tool="compute_factor",
            )
        if conn is None:
            return err_actionable(
                "workspace has no DuckDB",
                fix="call import_data first to populate the ohlcv table",
                tool="compute_factor",
            )

        try:
            prices_df = conn.execute(
                "SELECT date, asset, open, high, low, close, volume "
                "FROM ohlcv ORDER BY date, asset"
            ).fetch_df()
        except Exception as exc:                    # noqa: BLE001
            return err_actionable(
                f"ohlcv query failed: {exc}",
                fix="call import_data to create the ohlcv table; see workflow: get_market_data → import_data → compute_factor",
                tool="compute_factor",
            )

        if prices_df.empty:
            return err_actionable(
                "ohlcv table is empty",
                fix=(
                    "1) get_market_data(codes=['600519.SH'], "
                    "start_date='2023-01-01', end_date='2023-12-31', "
                    "strategy_name='default') fetches and persists OHLCV to "
                    "DuckDB in one step; 2) compute_factor(factor_code=...) again"
                ),
                tool="compute_factor",
            )

        # Pick asset (default: first)
        available_assets = sorted(prices_df["asset"].unique())
        if not available_assets:
            return err_actionable(
                "no assets in ohlcv table",
                fix="import data for at least one asset",
                tool="compute_factor",
            )
        if asset is None:
            asset = available_assets[0]
        elif asset not in available_assets:
            return err_actionable(
                f"asset '{asset}' not found",
                received=asset,
                expected=f"one of {available_assets[:10]}",
                fix="omit `asset` to use the first available, or pass a valid asset code",
                tool="compute_factor",
            )

        # Build single-asset wide DataFrame (date index, ohlcv columns)
        from ...tools.data_transforms import long_to_single_asset_wide

        asset_df = long_to_single_asset_wide(prices_df, asset=asset, value_cols="ohlcv")

        try:
            series = compute_factor(factor_code, asset_df, factor_name=factor_name)
        except FactorComputeError as exc:
            return err_actionable(
                str(exc),
                received=factor_code,
                fix=(
                    f"Use only available columns: {exc.available_columns}. "
                    f"Sample valid expressions: ts_return(close, 20), ts_std(close, 20), "
                    f"ts_mean(close, 60)"
                ),
                tool="compute_factor",
            )

        # Sample the result
        non_null = series.dropna()
        if len(non_null) == 0:
            return err_actionable(
                "factor produced no non-null values",
                received={"factor_code": factor_code, "asset": asset},
                fix="factor may need more data or different parameters",
                tool="compute_factor",
                extra={"factor_name": factor_name, "asset": asset},
            )
        sample = non_null.head(n_samples).to_dict()
        sample = {str(k): (None if v != v else float(v)) for k, v in sample.items()}

        return _ok({
            "factor_name": factor_name or "(unnamed)",
            "factor_code": factor_code,
            "asset": asset,
            "n_total": int(len(series)),
            "n_non_null": int(len(non_null)),
            "sample": sample,
            "first_date": str(series.index.min()) if len(series) else None,
            "last_date": str(series.index.max()) if len(series) else None,
        })


class GitDiffTool(BaseTool):
    """查看工作区 git 差异（默认未暂存；支持 staged/提交对比/路径过滤）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 查看 workspace 的 git diff。默认未暂存改动; staged=true 看暂存;
    # ref1/ref2 对比两个提交。
    #
    # ## 参数
    # - staged: 只看暂存改动 (默认 false)
    # - ref1/ref2: 提交对比 (需同时给)
    # - pathspec: 限定路径 (不能以 '-' 开头, 防参数注入)
    # - max_lines: 返回最大行数 (默认 200)
    #
    # ## 示例
    # {"pathspec": "strategies/momentum_20d/"}
    #
    # ## 边界
    # 只读工具; 要求 workspace 是 git 仓库; 超时 30s。
    #
    # ## 错误处理范式
    # - 非 git 仓库 → error + fix (git init)
    # - 超时 → fix 用 pathspec 缩小范围
    # - 均可安全重试
    #
    # ## 相关工具
    # read_file: 看具体文件; write_file: 修改后 diff
    # ─────────────────────────────────────────────
    """

    name = "git_diff"
    description = (
        "查看 workspace git diff (默认未暂存; staged/ref 对比/pathspec 可选)。"
    )
    repeatable = True
    category = "文件"

    def execute(
        self,
        ctx: ToolContext,
        staged: bool = False,
        ref1: str | None = None,
        ref2: str | None = None,
        pathspec: str | None = None,
        max_lines: int = 200,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="git_diff",
            )
        workspace = ctx.workspace

        cmd = ["git", "diff", "--no-color"]
        if staged:
            cmd.append("--staged")
        if ref1:
            cmd.append(ref1)
            if ref2:
                cmd.append(ref2)
        if pathspec:
            # Sanitize pathspec (basic guard against flag injection)
            if pathspec.startswith("-"):
                return err_actionable(
                    f"pathspec must not start with '-': {pathspec}",
                    received=pathspec,
                    fix="pass a relative path, e.g. pathspec='strategies/momentum_20d/'",
                    tool="git_diff",
                )
            cmd.extend(["--", pathspec])

        try:
            result = subprocess.run(
                cmd,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return err_actionable(
                "git diff timed out (30s)",
                fix="diff may be too large; try pathspec='strategies/<name>/' to limit scope",
                tool="git_diff",
            )
        except FileNotFoundError:
            return err_actionable(
                "git not found in PATH",
                fix="install git or check PATH",
                tool="git_diff",
            )

        if result.returncode != 0:
            return err_actionable(
                f"git diff returned {result.returncode}: {result.stderr.strip()}",
                fix="verify workspace is a git repo (git init if needed)",
                tool="git_diff",
            )

        diff = result.stdout
        lines = diff.splitlines()
        truncated = len(lines) > max_lines
        if truncated:
            diff = "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"

        return _ok({
            "diff": diff,
            "total_lines": len(lines),
            "truncated": truncated,
            "staged": staged,
        })


# ── 6. ListHistoryTool ──────────────────────────────────────────────


class ListHistoryTool(BaseTool):
    """列出历史回测记录（results.tsv，可按策略过滤，最新在前）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 查看过去的回测运行记录: 从 strategies/<name>/runs/results.tsv 读取
    # 摘要行 (含关键指标)。不指定 strategy_name 时找第一个 results.tsv。
    #
    # ## 参数
    # - strategy_name: 按策略过滤 (可选)
    # - limit: 最大返回行数 (默认 20)
    #
    # ## 示例
    # {"strategy_name": "momentum_20d"}
    #
    # ## 边界
    # 只读工具; 无 results.tsv 时返回空 runs + message。
    #
    # ## 错误处理范式
    # - 读取失败 → error + fix 检查权限
    # - 无记录不是错误 (返回空列表)
    #
    # ## 相关工具
    # run_backtest: 产生记录; drawdown_analysis: 深度分析
    # ─────────────────────────────────────────────
    """

    name = "list_history"
    description = (
        "列出历史回测记录 (results.tsv); strategy_name 过滤, limit 限量。"
    )
    repeatable = True
    category = "回测"

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str | None = None,
        limit: int = 20,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="list_history",
            )
        workspace = ctx.workspace

        results_path: Path | None = None
        if strategy_name:
            cand = workspace / "strategies" / strategy_name / "runs" / "results.tsv"
            if cand.exists():
                results_path = cand
        else:
            # Search all strategies for results.tsv
            strategies_dir = workspace / "strategies"
            if strategies_dir.exists():
                for d in sorted(strategies_dir.iterdir()):
                    cand = d / "runs" / "results.tsv"
                    if cand.exists():
                        results_path = cand
                        break

        if results_path is None or not results_path.exists():
            return _ok({
                "runs": [],
                "source": None,
                "message": "no results.tsv found",
            })

        try:
            with open(results_path, encoding="utf-8") as f:
                lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        except OSError as exc:
            return err_actionable(
                f"read failed: {exc}",
                fix="check file permissions on results.tsv",
                tool="list_history",
            )

        if not lines:
            return _ok({"runs": [], "source": str(results_path)})

        header = lines[0].split("\t")
        rows: list[dict[str, str]] = []
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) < len(header):
                continue
            row = {h: parts[i] for i, h in enumerate(header)}
            rows.append(row)

        # Sort by run name desc (newest first) and apply limit
        rows.sort(key=lambda r: r.get("run", ""), reverse=True)
        rows = rows[:limit]

        return _ok({
            "source": str(results_path),
            "n_rows": len(rows),
            "runs": rows,
        })


# ── 7. FactorAnalysisTool ──────────────────────────────────────────


class FactorAnalysisTool(BaseTool):
    """分析因子 IC/IR 统计（单资产）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 对因子表达式做 IC/IR 分析: 计算 IC mean、spearman IC、观测数。
    # 需要 workspace DuckDB 有价格数据。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填)
    # - asset: 资产代码 (默认第一个可用)
    # - forward_days: 前向收益天数 (默认 5)
    #
    # ## 示例
    # {"factor_code": "ts_return(close, 20)"}
    #
    # ## 边界
    # 只读工具; 观测数 < 10 时返回 insufficient data 错误。
    #
    # ## 错误处理范式
    # - 无 DB/空表 → error + workflow 提示
    # - asset 不存在 → error + expected 可用资产
    # - 数据不足 → error + 需要 >= 10 行
    # - 均可安全重试
    #
    # ## 相关工具
    # compute_factor: 单因子计算; factor_quintile_returns 等: 深入分析
    # ─────────────────────────────────────────────
    """

    name = "factor_analysis"
    description = (
        "对因子表达式做 IC/IR 分析 (IC mean / spearman IC / 观测数)。"
    )
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        asset: str | None = None,
        forward_days: int = 5,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="factor_analysis",
            )
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable("missing or invalid 'factor_code'", tool="factor_analysis")
        forward_days = int(forward_days)

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"db open failed: {exc}", tool="factor_analysis")

        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="factor_analysis")

        try:
            prices_df = conn.execute(
                "SELECT date, asset, close FROM ohlcv ORDER BY date, asset"
            ).fetch_df()
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"ohlcv query failed: {exc}", tool="factor_analysis")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="factor_analysis")

        available_assets = sorted(prices_df["asset"].unique())
        if asset is None:
            asset = available_assets[0]
        elif asset not in available_assets:
            return err_actionable(f"asset '{asset}' not found", tool="factor_analysis")

        asset_df = prices_df[prices_df["asset"] == asset].copy()
        asset_df = asset_df.drop_duplicates(subset=["date"], keep="last")
        asset_df = asset_df.set_index("date")[["close"]]
        asset_df = asset_df.sort_index()

        try:
            factor_series = compute_factor(factor_code, asset_df)
        except FactorComputeError as exc:
            return err_actionable(
                str(exc),
                tool="factor_analysis",
            )
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"compute failed: {exc}", tool="factor_analysis")

        # Compute forward returns
        asset_df["fwd_ret"] = asset_df["close"].pct_change(forward_days).shift(-forward_days)

        # Align and compute IC
        import pandas as pd
        aligned = pd.concat([factor_series, asset_df["fwd_ret"]], axis=1).dropna()
        if len(aligned) < 10:
            return err_actionable("insufficient data for IC analysis (need >= 10 rows)", tool="factor_analysis")

        ic = aligned.iloc[:, 0].corr(aligned["fwd_ret"])
        ic_mean = float(aligned.iloc[:, 0].corr(aligned["fwd_ret"], method="spearman")) if len(aligned) > 5 else 0.0

        return _ok({
            "factor_code": factor_code,
            "asset": asset,
            "forward_days": forward_days,
            "ic_mean": round(ic, 4) if pd.notna(ic) else None,
            "spearman_ic": round(ic_mean, 4),
            "n_observations": len(aligned),
        })


# ── 8. PatternRecognitionTool ──────────────────────────────────────


class PatternRecognitionTool(BaseTool):
    """识别价格形态（头肩/双顶底/趋势线/支撑阻力）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 从 DuckDB ohlcv 读取最近 N 根 K 线, 用简化启发式检测价格形态:
    # 均线趋势 (MA5 vs MA20)、近阻力/近支撑 (接近近期高低点 2% 内)、
    # 波动率挤压 (近 5 日标准差 < 近 20 日的 60%)。非严格形态识别,
    # 输出带置信度, 作为研究输入而非交易信号。
    #
    # ## 参数
    # - asset: 限定单个资产代码 (可选; 缺省分析全部资产)
    # - lookback: 分析的 K 线数量 (默认 60)
    #
    # ## 示例
    # {"asset": "600519.SH", "lookback": 120}
    #
    # ## 边界
    # 只读工具; 需要 workspace 含 DuckDB 且 ohlcv 非空; 数据量 < 10 根
    # 报 insufficient data。
    #
    # ## 错误处理范式
    # - 缺 workspace / 库不可用 / ohlcv 为空 → error, 先入库
    # - 数据不足 (< 10 根) → error, 需 get_market_data(persist=True)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: get_market_data / import_data; 同类: compute_factor
    # ─────────────────────────────────────────────────────────────
    """

    name = "pattern_recognition"
    description = "识别常见图表形态 (头肩顶底/双顶底/趋势线/支撑阻力); 需要 DuckDB 价格数据。"
    repeatable = True
    category = "分析"

    def execute(
        self,
        ctx: ToolContext,
        asset: str | None = None,
        lookback: int = 60,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="pattern_recognition")
        workspace = ctx.workspace
        lookback = int(lookback)

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"db open failed: {exc}", tool="pattern_recognition")

        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="pattern_recognition")

        try:
            prices_df = conn.execute(
                "SELECT date, asset, open, high, low, close, volume FROM ohlcv ORDER BY date"
            ).fetch_df()
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"ohlcv query failed: {exc}", tool="pattern_recognition")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="pattern_recognition")

        if asset:
            prices_df = prices_df[prices_df["asset"] == asset]

        prices_df = prices_df.tail(lookback)
        if len(prices_df) < 10:
            return err_actionable("insufficient data", tool="pattern_recognition")

        closes = prices_df["close"].values
        highs = prices_df["high"].values
        lows = prices_df["low"].values

        patterns = []

        # Simple trend detection
        if len(closes) >= 20:
            ma20 = closes[-20:].mean()
            ma5 = closes[-5:].mean() if len(closes) >= 5 else ma20
            if ma5 > ma20:
                patterns.append({"pattern": "uptrend", "confidence": 0.6})
            elif ma5 < ma20:
                patterns.append({"pattern": "downtrend", "confidence": 0.6})

        # Support/Resistance
        recent_high = float(highs.max())
        recent_low = float(lows.min())
        current = float(closes[-1])
        range_pct = (recent_high - recent_low) / recent_high * 100 if recent_high > 0 else 0

        if current >= recent_high * 0.98:
            patterns.append({"pattern": "near_resistance", "level": round(recent_high, 2), "confidence": 0.5})
        if current <= recent_low * 1.02:
            patterns.append({"pattern": "near_support", "level": round(recent_low, 2), "confidence": 0.5})

        # Volatility squeeze
        if len(closes) >= 20:
            std20 = float(closes[-20:].std())
            std5 = float(closes[-5:].std()) if len(closes) >= 5 else std20
            if std5 < std20 * 0.6:
                patterns.append({"pattern": "volatility_squeeze", "confidence": 0.5})

        return _ok({
            "asset": asset or "(all)",
            "lookback": lookback,
            "current_price": round(current, 2),
            "range_pct": round(range_pct, 2),
            "patterns": patterns,
        })


# ── 9. ListSkillsTool ─────────────────────────────────────────────


class ListSkillsTool(BaseTool):
    """列出可用技能（名称 + 一句话描述）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 列出方法论技能: workspace/.skills/ 优先, 合并内置
    # templates/.skills/, 返回名称/类别/一句话描述, 可按类别过滤。
    # 技能全文用 load_skill 按需加载, 避免大全文直接进 prompt。
    #
    # ## 参数
    # - category: 按类别过滤 (可选; 缺省返回全部)
    #
    # ## 示例
    # {"category": "因子研究"}
    #
    # ## 边界
    # 只读工具; 需要 workspace 上下文; 无技能时返回空列表 (非错误)。
    #
    # ## 错误处理范式
    # - 缺 workspace 上下文 → error, 需 AgentLoop 注入
    # - 扫描/加载异常 → error + 异常信息, 可重试
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # load_skill: 加载技能全文; tool_help: 同类按需加载机制
    # ─────────────────────────────────────────────────────────────
    """

    name = "list_skills"
    description = "列出全部方法技能: 名称/类别/一句话描述; load_skill 获取全文。"
    repeatable = True
    category = "技能"

    def execute(
        self,
        ctx: ToolContext,
        category: str | None = None,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="list_skills")
        workspace = ctx.workspace

        try:
            from ...skills import SkillRegistry
            registry = SkillRegistry()

            # Load from workspace .skills/ first, then bundled templates
            workspace_skills = workspace / ".skills"
            if workspace_skills.is_dir():
                registry.load_directory(workspace_skills)

            bundled_skills = Path(__file__).parent.parent.parent / "templates" / ".skills"
            if bundled_skills.is_dir():
                registry.load_directory(bundled_skills)

            if category:
                skills = registry.by_category(category)
            else:
                skills = registry.list_all()

            skill_list = [
                {
                    "name": s.name,
                    "category": s.category,
                    "description": s.description[:120] if s.description else "",
                }
                for s in skills
            ]

            return _ok({
                "n_skills": len(skill_list),
                "categories": registry.categories(),
                "skills": skill_list,
            })
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"list_skills failed: {exc}", tool="list_skills")


# ── 10. LoadSkillTool ─────────────────────────────────────────────


class LoadSkillTool(BaseTool):
    """按名称加载技能全文。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 按名称加载技能的完整 markdown 全文 (含 API 契约/工作流/示例),
    # 供 agent 按方法论执行。workspace/.skills/ 覆盖同名内置技能。
    # 先 list_skills 浏览目录, 再决定加载哪个。
    #
    # ## 参数
    # - name: 技能名 (必填)
    #
    # ## 示例
    # {"name": "factor-research"}
    #
    # ## 边界
    # 只读工具; 需要 workspace 上下文; name 为空/非字符串报错。
    #
    # ## 错误处理范式
    # - name 缺失/非法 → error + 提示
    # - 技能不存在 → error + available 列表 (最多 20 个)
    # - 内部异常 → error + 异常信息, 可重试
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # list_skills: 目录浏览; tool_help: 同类按需加载机制
    # ─────────────────────────────────────────────────────────────
    """

    name = "load_skill"
    description = "按名称加载技能完整 markdown 文档 (含 API 契约/工作流/示例)。"
    repeatable = True
    category = "技能"

    def execute(
        self,
        ctx: ToolContext,
        name: str,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="load_skill")
        workspace = ctx.workspace
        if not isinstance(name, str) or not name:
            return err_actionable("missing or invalid 'name'", tool="load_skill")

        try:
            from ...skills import SkillRegistry
            registry = SkillRegistry()

            # Load from workspace .skills/ first (user overrides), then bundled
            workspace_skills = workspace / ".skills"
            if workspace_skills.is_dir():
                registry.load_directory(workspace_skills)

            bundled_skills = Path(__file__).parent.parent.parent / "templates" / ".skills"
            if bundled_skills.is_dir():
                registry.load_directory(bundled_skills)

            skill = registry.get(name)
            if skill is None:
                available = [s.name for s in registry.list_all()][:20]
                return err_actionable(
                    f"skill '{name}' not found",
                    available=available,
                )

            return _ok({
                "name": skill.name,
                "category": skill.category,
                "description": skill.description,
                "tags": skill.tags,
                "content": skill.content,
            })
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"load_skill failed: {exc}", tool="load_skill")


# ── 11. OptionsPricingTool ──────────────────────────────────────────


class OptionsPricingTool(BaseTool):
    """Black-Scholes 期权定价与 Greeks。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 用 Black-Scholes 公式计算欧式期权理论价与 Greeks
    # (delta/gamma/theta/vega/rho), 用于研究中的敏感度分析。
    # 仅支持欧式期权, 不处理分红与美式提前行权。
    #
    # ## 参数
    # - spot/strike/rate/volatility/time_to_expiry: 标的价/行权价/
    #   无风险利率/波动率/剩余期限 (年), 均须为正
    # - option_type: call 或 put (默认 call)
    #
    # ## 示例
    # {"spot": 100.0, "strike": 105.0, "rate": 0.03, "volatility": 0.25,
    #  "time_to_expiry": 0.5, "option_type": "call"}
    #
    # ## 边界
    # 只读工具; 无需 workspace/数据库; 需 scipy; strict 工具 (schema
    # 由 strict 模式强制必填)。
    #
    # ## 错误处理范式
    # - option_type 非 call/put → error + 枚举提示, 修正后重试
    # - 任一参数非正 → error + 提示, 修正后重试
    # - 幂等: 纯函数计算
    #
    # ## 相关工具
    # pattern_recognition: 行情形态分析 (研究输入)
    # ─────────────────────────────────────────────────────────────
    """

    name = "options_pricing"
    description = "计算 Black-Scholes 期权价格与 Greeks (delta/gamma/theta/vega/rho)。"
    repeatable = True
    strict = True  # Simple shape — OpenAI strict mode applies cleanly
    category = "分析"

    def execute(
        self,
        ctx: ToolContext,
        spot: float,
        strike: float,
        rate: float,
        volatility: float,
        time_to_expiry: float,
        option_type: str = "call",
    ) -> str:
        spot = float(spot)
        strike = float(strike)
        rate = float(rate)
        vol = float(volatility)
        T = float(time_to_expiry)
        option_type = option_type.lower()

        if option_type not in ("call", "put"):
            return err_actionable("option_type must be 'call' or 'put'", tool="options_pricing")
        if T <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
            return err_actionable("spot, strike, volatility, and time_to_expiry must be positive", tool="options_pricing")

        from math import exp, log, sqrt

        from scipy.stats import norm

        d1 = (log(spot / strike) + (rate + 0.5 * vol**2) * T) / (vol * sqrt(T))
        d2 = d1 - vol * sqrt(T)

        if option_type == "call":
            price = spot * norm.cdf(d1) - strike * exp(-rate * T) * norm.cdf(d2)
            delta = float(norm.cdf(d1))
        else:
            price = strike * exp(-rate * T) * norm.cdf(-d2) - spot * norm.cdf(-d1)
            delta = float(norm.cdf(d1) - 1)

        gamma = float(norm.pdf(d1) / (spot * vol * sqrt(T)))
        theta = float(
            -(spot * norm.pdf(d1) * vol) / (2 * sqrt(T))
            - rate * strike * exp(-rate * T) * norm.cdf(d2 if option_type == "call" else -d2)
        )
        vega = float(spot * norm.pdf(d1) * sqrt(T) / 100)
        rho = float(
            strike * T * exp(-rate * T) * norm.cdf(d2 if option_type == "call" else -d2) / 100
        )

        return _ok({
            "option_type": option_type,
            "spot": spot,
            "strike": strike,
            "rate": rate,
            "volatility": vol,
            "time_to_expiry": T,
            "price": round(price, 4),
            "delta": round(delta, 4),
            "gamma": round(gamma, 4),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
            "rho": round(rho, 4),
        })


# ── 12. FactorCrossSectionalAnalysis ──────────────────────────────────


class FactorCrossSectionalAnalysis(BaseTool):
    """截面 IC 分析（全资产池，Pearson/Spearman）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 对资产池计算因子表达式的逐日截面 IC (Pearson + Spearman), 汇总
    # IC 均值/标准差/IR/IC>0 比例, 并附前 5 个样本日期。验证因子在
    # 横截面上是否有区分度。单资产验证用 factor_analysis。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填, 语法见 .skills/factor-research.md)
    # - universe: 逗号分隔代码或 all (默认 all)
    # - start_date/end_date: 数据时间窗 (可选, ISO 日期)
    # - forward_days: 前向收益窗口天数 (默认 5)
    #
    # ## 示例
    # {"factor_code": "ts_mean(close,20)/ts_mean(close,60)-1",
    #  "universe": "600519.SH,000858.SZ,000001.SZ"}
    #
    # ## 边界
    # 只读工具; 需要 DuckDB ohlcv 数据; 需 ≥3 资产且 ≥3 个因子计算
    # 成功; 有效 IC 观测 ≥5; 样本 < 20 根 K 线的资产被跳过。
    #
    # ## 错误处理范式
    # - universe 含不存在代码 → error + 缺失列表
    # - 资产数/因子成功数 < 3 → error, 需先入库更多资产
    # - IC 观测 < 5 → error "too few valid IC observations"
    # - ohlcv 为空/库不可用 → error, 先 get_market_data(persist=True)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: get_market_data; 后续: factor_quintile_returns / factor_ic_decay;
    # 同类: factor_analysis (单资产)
    # ─────────────────────────────────────────────────────────────
    """

    name = "factor_cross_sectional_analysis"
    description = "对资产池计算因子表达式的截面 IC (Pearson/Spearman): IC mean/std/IR/IC>0 比例, 含日度 IC 序列。"
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        universe: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        forward_days: int = 5,
    ) -> str:
        import numpy as np

        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="factor_cross_sectional_analysis")
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable("missing or invalid 'factor_code'", tool="factor_cross_sectional_analysis")
        universe_str = universe
        forward_days = int(forward_days)

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:
            return err_actionable(f"db open failed: {exc}", tool="factor_cross_sectional_analysis")
        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="factor_cross_sectional_analysis")

        try:
            query = "SELECT date, asset, open, high, low, close, volume FROM ohlcv"
            clauses = []
            if start_date:
                clauses.append(f"date >= '{start_date}'")
            if end_date:
                clauses.append(f"date <= '{end_date}'")
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY date, asset"
            prices_df = conn.execute(query).fetch_df()
        except Exception as exc:
            return err_actionable(f"ohlcv query failed: {exc}", tool="factor_cross_sectional_analysis")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="factor_cross_sectional_analysis")

        # Filter universe
        all_assets = sorted(prices_df["asset"].unique())
        if universe_str != "all":
            assets = [a.strip() for a in universe_str.split(",")]
            missing = [a for a in assets if a not in all_assets]
            if missing:
                return err_actionable(f"assets not found: {missing[:5]}", tool="factor_cross_sectional_analysis")
        else:
            assets = all_assets

        if len(assets) < 3:
            return err_actionable(f"need >= 3 assets for cross-sectional IC, got {len(assets)}", tool="factor_cross_sectional_analysis")

        df = prices_df[prices_df["asset"].isin(assets)].copy()

        # Compute factor per asset and build date×asset panel
        import pandas as pd
        from ...tools.data_transforms import long_to_single_asset_wide

        factor_panel = {}
        for asset_code in assets:
            adf = long_to_single_asset_wide(df, asset=asset_code, value_cols="ohlcv")
            if len(adf) < 20:
                continue
            try:
                fv = compute_factor(factor_code, adf)
                # Deduplicate index to avoid reindex errors
                if hasattr(fv, 'index') and fv.index.duplicated().any():
                    fv = fv[~fv.index.duplicated(keep='first')]
                factor_panel[asset_code] = fv
            except Exception:
                continue

        if len(factor_panel) < 3:
            return err_actionable(f"factor computation succeeded on < 3 assets ({len(factor_panel)})", tool="factor_cross_sectional_analysis")

        # Build forward return panel
        from ...tools.data_transforms import long_to_single_asset_wide

        ret_panel = {}
        for asset_code in factor_panel:
            adf = long_to_single_asset_wide(df, asset=asset_code, value_cols="close")
            ret_panel[asset_code] = adf["close"].pct_change(forward_days).shift(-forward_days)

        # Compute daily cross-sectional IC
        factor_df = pd.DataFrame(factor_panel)
        ret_df = pd.DataFrame(ret_panel)
        common_dates = factor_df.index.intersection(ret_df.index)
        factor_df = factor_df.loc[common_dates]
        ret_df = ret_df.loc[common_dates]

        ic_pearson_list = []
        ic_spearman_list = []
        valid_dates = []
        for dt in common_dates:
            fv = factor_df.loc[dt].dropna()
            rv = ret_df.loc[dt].dropna()
            common = fv.index.intersection(rv.index)
            if len(common) < 3:
                continue
            f_vals = fv[common]
            r_vals = rv[common]
            pearson_ic = f_vals.corr(r_vals)
            spearman_ic = f_vals.corr(r_vals, method="spearman")
            if pd.notna(pearson_ic):
                ic_pearson_list.append(pearson_ic)
                ic_spearman_list.append(spearman_ic)
                valid_dates.append(dt)

        if len(ic_pearson_list) < 5:
            return err_actionable(f"too few valid IC observations ({len(ic_pearson_list)})", tool="factor_cross_sectional_analysis")

        ic_arr = np.array(ic_pearson_list)
        spear_arr = np.array(ic_spearman_list)

        return _ok({
            "factor_code": factor_code,
            "n_assets": len(factor_panel),
            "n_dates": len(ic_pearson_list),
            "forward_days": forward_days,
            "ic_pearson_mean": round(float(np.mean(ic_arr)), 4),
            "ic_pearson_std": round(float(np.std(ic_arr)), 4),
            "ir": round(float(np.mean(ic_arr) / np.std(ic_arr)), 4) if np.std(ic_arr) > 0 else None,
            "ic_pearson_gt0_ratio": round(float(np.mean(ic_arr > 0)), 4),
            "ic_spearman_mean": round(float(np.mean(spear_arr)), 4),
            "ic_spearman_std": round(float(np.std(spear_arr)), 4),
            "sample_dates": [str(d) for d in valid_dates[:5]],
        })


# ── 13. FactorQuintileReturns ──────────────────────────────────────────


class FactorQuintileReturns(BaseTool):
    """因子分层组合收益分析（quintile 分组）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 把资产池按因子值逐日分为 N 组 (默认 5 组), 计算各组的平均前向
    # 收益 (holding_period 天) 与多空价差 (Qn - Q1), 检验因子分组
    # 单调性。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填)
    # - universe: 逗号分隔代码或 all (默认 all)
    # - start_date/end_date: 数据时间窗 (可选)
    # - n_groups: 分组数 (默认 5)
    # - holding_period: 前向收益持有天数 (默认 5)
    #
    # ## 示例
    # {"factor_code": "ts_rank(close,20)", "n_groups": 5, "holding_period": 5}
    #
    # ## 边界
    # 只读工具; 需要 DuckDB ohlcv; 资产数须 ≥ n_groups*2; 样本 < 20 根
    # 或因子计算失败的资产被跳过; 某日有效资产不足则跳过该日。
    #
    # ## 错误处理范式
    # - 资产不足 n_groups*2 → error + 所需/实有数量
    # - ohlcv 为空 → error, 先入库
    # - 某组无观测 → 该组 mean_return 为 null (非整体失败)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: get_market_data; 后续: factor_ic_decay / factor_turnover;
    # 同类: factor_cross_sectional_analysis
    # ─────────────────────────────────────────────────────────────
    """

    name = "factor_quintile_returns"
    description = "把资产池按因子值分 N 组, 计算各组的平均前向收益与多空价差。"
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        universe: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        n_groups: int = 5,
        holding_period: int = 5,
    ) -> str:
        import numpy as np

        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="factor_quintile_returns")
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable("missing or invalid 'factor_code'", tool="factor_quintile_returns")
        universe_str = universe
        n_groups = int(n_groups)
        holding_period = int(holding_period)

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:
            return err_actionable(f"db open failed: {exc}", tool="factor_quintile_returns")
        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="factor_quintile_returns")

        try:
            query = "SELECT date, asset, open, high, low, close, volume FROM ohlcv"
            clauses = []
            if start_date:
                clauses.append(f"date >= '{start_date}'")
            if end_date:
                clauses.append(f"date <= '{end_date}'")
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY date, asset"
            prices_df = conn.execute(query).fetch_df()
        except Exception as exc:
            return err_actionable(f"ohlcv query failed: {exc}", tool="factor_quintile_returns")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="factor_quintile_returns")

        all_assets = sorted(prices_df["asset"].unique())
        if universe_str != "all":
            assets = [a.strip() for a in universe_str.split(",")]
        else:
            assets = all_assets

        if len(assets) < n_groups * 2:
            return err_actionable(f"need >= {n_groups * 2} assets for {n_groups}-group analysis, got {len(assets)}", tool="factor_quintile_returns")

        import pandas as pd
        df = prices_df[prices_df["asset"].isin(assets)].copy()

        # Compute factor per asset
        from ...tools.data_transforms import long_to_single_asset_wide

        factor_panel = {}
        for asset_code in assets:
            adf = long_to_single_asset_wide(df, asset=asset_code, value_cols="ohlcv")
            if len(adf) < 20:
                continue
            try:
                fv = compute_factor(factor_code, adf)
                # Deduplicate index to avoid reindex errors
                if hasattr(fv, 'index') and fv.index.duplicated().any():
                    fv = fv[~fv.index.duplicated(keep='first')]
                factor_panel[asset_code] = fv
            except Exception:
                continue

        # Forward return panel
        from ...tools.data_transforms import long_to_single_asset_wide

        ret_panel = {}
        for asset_code in factor_panel:
            adf = long_to_single_asset_wide(df, asset=asset_code, value_cols="close")
            ret_panel[asset_code] = adf["close"].pct_change(holding_period).shift(-holding_period)

        factor_df = pd.DataFrame(factor_panel)
        ret_df = pd.DataFrame(ret_panel)
        common_dates = factor_df.index.intersection(ret_df.index)
        factor_df = factor_df.loc[common_dates]
        ret_df = ret_df.loc[common_dates]

        # Assign quintile groups per date and compute group returns
        group_returns = {g: [] for g in range(n_groups)}
        for dt in common_dates:
            fv = factor_df.loc[dt].dropna()
            rv = ret_df.loc[dt].dropna()
            common = fv.index.intersection(rv.index)
            if len(common) < n_groups * 2:
                continue
            fv_sorted = fv[common].sort_values()
            n_per = len(fv_sorted) // n_groups
            for g in range(n_groups):
                start_idx = g * n_per
                end_idx = start_idx + n_per if g < n_groups - 1 else len(fv_sorted)
                group_assets = fv_sorted.index[start_idx:end_idx]
                g_ret = rv[group_assets].mean()
                if pd.notna(g_ret):
                    group_returns[g].append(float(g_ret))

        result = {}
        for g in range(n_groups):
            rets = group_returns[g]
            if rets:
                result[f"Q{g+1}_mean_return"] = round(float(np.mean(rets)), 6)
                result[f"Q{g+1}_n_periods"] = len(rets)
            else:
                result[f"Q{g+1}_mean_return"] = None
                result[f"Q{g+1}_n_periods"] = 0

        q1 = result.get("Q1_mean_return")
        qn = result.get(f"Q{n_groups}_mean_return")
        if q1 is not None and qn is not None:
            result["long_short_spread"] = round(qn - q1, 6)

        return _ok({
            "factor_code": factor_code,
            "n_groups": n_groups,
            "holding_period": holding_period,
            "n_assets_used": len(factor_panel),
            **result,
        })


# ── 14. FactorICDecay ──────────────────────────────────────────────────


class FactorICDecay(BaseTool):
    """因子 IC 衰减曲线（多前向周期）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 计算因子在多个前向收益周期 (默认 1,5,10,20,60 天) 的逐日截面
    # Spearman IC 均值/标准差/IR, 观察预测力随周期的衰减速度,
    # 用于选择因子最佳持有周期。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填)
    # - universe: 逗号分隔代码或 all (默认 all)
    # - start_date/end_date: 数据时间窗 (可选)
    # - horizons: 逗号分隔的前向周期列表 (默认 1,5,10,20,60)
    #
    # ## 示例
    # {"factor_code": "ts_mean(close,20)/ts_mean(close,60)-1",
    #  "horizons": "5,10,20"}
    #
    # ## 边界
    # 只读工具; 需要 DuckDB ohlcv; 因子计算成功资产须 ≥3; 单日截面
    # 有效资产 < 3 则跳过该日。
    #
    # ## 错误处理范式
    # - 因子成功资产 < 3 → error
    # - 某 horizon 无有效观测 → 该周期 ic_mean 等为 null (非整体失败)
    # - ohlcv 为空 → error, 先入库
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: get_market_data; 后续: 按最佳 horizon 构建策略;
    # 同类: factor_cross_sectional_analysis / factor_turnover
    # ─────────────────────────────────────────────────────────────
    """

    name = "factor_ic_decay"
    description = "计算因子在多个前向收益周期 (如 1,5,10,20,60 天) 的截面 IC, 衡量预测力衰减速度。"
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        universe: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        horizons: str = "1,5,10,20,60",
    ) -> str:
        import numpy as np

        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="factor_ic_decay")
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable("missing or invalid 'factor_code'", tool="factor_ic_decay")
        universe_str = universe
        horizons_str = horizons
        horizons = [int(h.strip()) for h in horizons_str.split(",")]

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:
            return err_actionable(f"db open failed: {exc}", tool="factor_ic_decay")
        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="factor_ic_decay")

        try:
            query = "SELECT date, asset, open, high, low, close, volume FROM ohlcv"
            clauses = []
            if start_date:
                clauses.append(f"date >= '{start_date}'")
            if end_date:
                clauses.append(f"date <= '{end_date}'")
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY date, asset"
            prices_df = conn.execute(query).fetch_df()
        except Exception as exc:
            return err_actionable(f"ohlcv query failed: {exc}", tool="factor_ic_decay")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="factor_ic_decay")

        all_assets = sorted(prices_df["asset"].unique())
        if universe_str != "all":
            assets = [a.strip() for a in universe_str.split(",")]
        else:
            assets = all_assets

        import pandas as pd
        df = prices_df[prices_df["asset"].isin(assets)].copy()

        # Compute factor per asset
        from ...tools.data_transforms import long_to_single_asset_wide

        factor_panel = {}
        for asset_code in assets:
            adf = long_to_single_asset_wide(df, asset=asset_code, value_cols="ohlcv")
            if len(adf) < 20:
                continue
            try:
                fv = compute_factor(factor_code, adf)
                # Deduplicate index to avoid reindex errors
                if hasattr(fv, 'index') and fv.index.duplicated().any():
                    fv = fv[~fv.index.duplicated(keep='first')]
                factor_panel[asset_code] = fv
            except Exception:
                continue

        if len(factor_panel) < 3:
            return err_actionable(f"factor computation succeeded on < 3 assets ({len(factor_panel)})", tool="factor_ic_decay")

        factor_df = pd.DataFrame(factor_panel)

        # Compute IC at each horizon
        results = []
        for h in horizons:
            from ...tools.data_transforms import long_to_single_asset_wide

            ret_panel = {}
            for asset_code in factor_panel:
                adf = long_to_single_asset_wide(df, asset=asset_code, value_cols="close")
                ret_panel[asset_code] = adf["close"].pct_change(h).shift(-h)

            ret_df = pd.DataFrame(ret_panel)
            common_dates = factor_df.index.intersection(ret_df.index)
            f_df = factor_df.loc[common_dates]
            r_df = ret_df.loc[common_dates]

            ic_list = []
            for dt in common_dates:
                fv = f_df.loc[dt].dropna()
                rv = r_df.loc[dt].dropna()
                common = fv.index.intersection(rv.index)
                if len(common) < 3:
                    continue
                ic = fv[common].corr(rv[common], method="spearman")
                if pd.notna(ic):
                    ic_list.append(ic)

            if ic_list:
                arr = np.array(ic_list)
                results.append({
                    "horizon": h,
                    "ic_mean": round(float(np.mean(arr)), 4),
                    "ic_std": round(float(np.std(arr)), 4),
                    "ir": round(float(np.mean(arr) / np.std(arr)), 4) if np.std(arr) > 0 else None,
                    "n_periods": len(ic_list),
                })
            else:
                results.append({"horizon": h, "ic_mean": None, "ic_std": None, "ir": None, "n_periods": 0})

        return _ok({
            "factor_code": factor_code,
            "n_assets": len(factor_panel),
            "ic_decay": results,
        })


# ── 15. FactorTurnover ─────────────────────────────────────────────────


class FactorTurnover(BaseTool):
    """因子排名换手率分析（排名稳定性）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 按 rebalance_freq 天间隔采样因子值, 计算相邻采样日资产排名的
    # Spearman 相关, 换手率 = 1 - 秩相关; 输出平均/中位换手与排名
    # 稳定度 (1 - 平均换手)。低换手因子排名稳定, 更适合实盘。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填)
    # - universe: 逗号分隔代码或 all (默认 all)
    # - start_date/end_date: 数据时间窗 (可选)
    # - rebalance_freq: 采样间隔天数 (默认 5)
    #
    # ## 示例
    # {"factor_code": "ts_mean(close,20)/ts_mean(close,60)-1",
    #  "rebalance_freq": 10}
    #
    # ## 边界
    # 只读工具; 需要 DuckDB ohlcv; 因子成功资产须 ≥3; 采样期 < 2 报错;
    # 相邻采样日公共资产 < 3 的间隔被跳过。
    #
    # ## 错误处理范式
    # - 采样期 < 2 → error "not enough rebalancing periods"
    # - 无有效换手观测 → error "no valid turnover observations"
    # - 因子成功资产 < 3 → error
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: get_market_data; 同类: factor_ic_decay / factor_quintile_returns
    # ─────────────────────────────────────────────────────────────
    """

    name = "factor_turnover"
    description = "衡量因子排名随时间的变化: 相邻调仓期的平均秩相关; 低换手 = 因子稳定。"
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        universe: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        rebalance_freq: int = 5,
    ) -> str:
        import numpy as np

        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="factor_turnover")
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable("missing or invalid 'factor_code'", tool="factor_turnover")
        universe_str = universe
        rebalance_freq = int(rebalance_freq)

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:
            return err_actionable(f"db open failed: {exc}", tool="factor_turnover")
        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="factor_turnover")

        try:
            query = "SELECT date, asset, open, high, low, close, volume FROM ohlcv"
            clauses = []
            if start_date:
                clauses.append(f"date >= '{start_date}'")
            if end_date:
                clauses.append(f"date <= '{end_date}'")
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY date, asset"
            prices_df = conn.execute(query).fetch_df()
        except Exception as exc:
            return err_actionable(f"ohlcv query failed: {exc}", tool="factor_turnover")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="factor_turnover")

        all_assets = sorted(prices_df["asset"].unique())
        if universe_str != "all":
            assets = [a.strip() for a in universe_str.split(",")]
        else:
            assets = all_assets

        import pandas as pd
        df = prices_df[prices_df["asset"].isin(assets)].copy()

        # Compute factor per asset
        from ...tools.data_transforms import long_to_single_asset_wide

        factor_panel = {}
        for asset_code in assets:
            adf = long_to_single_asset_wide(df, asset=asset_code, value_cols="ohlcv")
            if len(adf) < 20:
                continue
            try:
                fv = compute_factor(factor_code, adf)
                # Deduplicate index to avoid reindex errors
                if hasattr(fv, 'index') and fv.index.duplicated().any():
                    fv = fv[~fv.index.duplicated(keep='first')]
                factor_panel[asset_code] = fv
            except Exception:
                continue

        if len(factor_panel) < 3:
            return err_actionable(f"factor computation succeeded on < 3 assets ({len(factor_panel)})", tool="factor_turnover")

        factor_df = pd.DataFrame(factor_panel)

        # Sample dates at rebalance frequency
        dates = sorted(factor_df.index)
        sampled_dates = dates[::rebalance_freq]
        if len(sampled_dates) < 2:
            return err_actionable("not enough rebalancing periods", tool="factor_turnover")

        # Compute rank correlation between consecutive periods
        turnover_list = []
        for i in range(1, len(sampled_dates)):
            prev_ranks = factor_df.loc[sampled_dates[i - 1]].dropna().rank()
            curr_ranks = factor_df.loc[sampled_dates[i]].dropna().rank()
            common = prev_ranks.index.intersection(curr_ranks.index)
            if len(common) < 3:
                continue
            rank_corr = prev_ranks[common].corr(curr_ranks[common], method="spearman")
            if pd.notna(rank_corr):
                turnover_list.append(1.0 - float(rank_corr))

        if not turnover_list:
            return err_actionable("no valid turnover observations", tool="factor_turnover")

        arr = np.array(turnover_list)
        return _ok({
            "factor_code": factor_code,
            "n_assets": len(factor_panel),
            "n_periods": len(turnover_list),
            "rebalance_freq_days": rebalance_freq,
            "avg_turnover": round(float(np.mean(arr)), 4),
            "median_turnover": round(float(np.median(arr)), 4),
            "std_turnover": round(float(np.std(arr)), 4),
            "avg_rank_stability": round(1.0 - float(np.mean(arr)), 4),
        })


# ── 16. StrategyCompare ────────────────────────────────────────────────


class StrategyCompare(BaseTool):
    """多策略指标横向对比。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 读取多个策略 runs/results.tsv 的最新一行, 按指定指标列横向对比,
    # 用于回测结果选优。缺失结果文件的策略带 error 字段, 不整体失败。
    #
    # ## 参数
    # - strategy_names: 逗号分隔的策略名列表 (必填)
    # - metrics: 逗号分隔的指标列 (默认
    #   sharpe,ann_return,max_dd,calmar,turnover,win_rate)
    #
    # ## 示例
    # {"strategy_names": "mom_20d,mom_60d", "metrics": "sharpe,ann_return,max_dd"}
    #
    # ## 边界
    # 只读工具; 需要 workspace; 各策略须已跑过回测 (results.tsv 存在);
    # 指标列不存在时该列为 null; 数值转浮点失败时保留原值。
    #
    # ## 错误处理范式
    # - strategy_names 缺失 → error
    # - 单策略 results.tsv 缺失/读取失败/无记录 → 该策略行带 error
    #   (非整体失败)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: run_backtest; 后续: drawdown_analysis / benchmark_comparison
    # ─────────────────────────────────────────────────────────────
    """

    name = "strategy_compare"
    description = "对比多个策略的回测指标 (读各策略 runs/results.tsv), 指标列可指定。"
    repeatable = True
    category = "回测"

    def execute(
        self,
        ctx: ToolContext,
        strategy_names: str,
        metrics: str = "sharpe,ann_return,max_dd,calmar,turnover,win_rate",
    ) -> str:
        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="strategy_compare")
        workspace = ctx.workspace
        strategy_names_str = strategy_names
        if not strategy_names_str:
            return err_actionable("missing 'strategy_names'", tool="strategy_compare")
        strategy_names = [s.strip() for s in strategy_names_str.split(",")]
        metrics_str = metrics
        metrics_keys = [m.strip() for m in metrics_str.split(",")]

        results = []
        for name in strategy_names:
            results_path = workspace / "strategies" / name / "runs" / "results.tsv"
            if not results_path.exists():
                results.append({"strategy": name, "error": f"results.tsv not found at {results_path}"})
                continue

            try:
                import csv
                with open(results_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f, delimiter="\t")
                    rows = list(reader)
            except Exception as exc:
                results.append({"strategy": name, "error": f"read failed: {exc}"})
                continue

            if not rows:
                results.append({"strategy": name, "error": "no runs found"})
                continue

            latest = rows[-1]
            row = {"strategy": name}
            for key in metrics_keys:
                val = latest.get(key)
                if val is not None:
                    try:
                        row[key] = round(float(val), 4)
                    except (ValueError, TypeError):
                        row[key] = val
                else:
                    row[key] = None
            row["run_name"] = latest.get("run_name", "")
            results.append(row)

        return _ok({
            "strategies": strategy_names,
            "metrics": metrics_keys,
            "comparison": results,
        })


# ── 17. DrawdownAnalysis ──────────────────────────────────────────────


class DrawdownAnalysis(BaseTool):
    """策略回撤深度分析（最大回撤/回撤期列表）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 从最新 run 的权益曲线计算回撤序列: 最大回撤、当前回撤、回撤期
    # 数量与按深度排序的 Top N 回撤区间 (含开始/谷底/恢复索引与时长)。
    # 依据回撤深度与恢复时长判断风控参数是否需要调整。
    #
    # ## 参数
    # - strategy_name: 策略名 (必填)
    # - top_n: 返回的回撤区间数量 (默认 5)
    #
    # ## 示例
    # {"strategy_name": "mom_20d", "top_n": 10}
    #
    # ## 边界
    # 只读工具; 需要 workspace; 最新 run 须含权益曲线
    # (equity.csv/equity_curve.csv/portfolio.csv/nav.csv 之一, 或
    # run.log 含 equity= 数值); 权益点 < 10 报错; 仍在回撤中的区间
    # recovery_idx 为 null。
    #
    # ## 错误处理范式
    # - runs 目录不存在/无 run → error
    # - 找不到权益曲线或点 < 10 → error, 检查 run 输出
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: run_backtest; 后续: benchmark_comparison / strategy_compare
    # ─────────────────────────────────────────────────────────────
    """

    name = "drawdown_analysis"
    description = "分析策略回撤期: 从最近 run 的权益曲线计算最大回撤与 Top N 回撤区间。"
    repeatable = True
    category = "回测"

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str,
        top_n: int = 5,
    ) -> str:
        import numpy as np

        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="drawdown_analysis")
        workspace = ctx.workspace
        if not strategy_name:
            return err_actionable("missing 'strategy_name'", tool="drawdown_analysis")
        top_n = int(top_n)

        # Find latest run
        runs_dir = workspace / "strategies" / strategy_name / "runs"
        if not runs_dir.exists():
            return err_actionable(f"runs directory not found: {runs_dir}", tool="drawdown_analysis")

        run_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()])
        if not run_dirs:
            return err_actionable("no runs found", tool="drawdown_analysis")

        latest_run = run_dirs[-1]

        # Try to find equity curve in common formats
        import pandas as pd
        equity = None
        for fname in ["equity.csv", "equity_curve.csv", "portfolio.csv", "nav.csv"]:
            fpath = latest_run / fname
            if fpath.exists():
                try:
                    eq_df = pd.read_csv(fpath)
                    # Try common column names
                    for col in ["equity", "nav", "portfolio_value", "value", "close"]:
                        if col in eq_df.columns:
                            equity = eq_df[col].values
                            dates = eq_df.iloc[:, 0].values if len(eq_df.columns) > 1 else None
                            break
                    if equity is not None:
                        break
                except Exception:
                    continue

        if equity is None:
            # Try run.log for equity data
            log_path = latest_run / "run.log"
            if log_path.exists():
                try:
                    log_text = log_path.read_text(encoding="utf-8")
                    # Look for equity values in log
                    import re
                    eq_matches = re.findall(r"equity[=:]\s*([\d.]+)", log_text)
                    if eq_matches:
                        equity = np.array([float(v) for v in eq_matches])
                except Exception:
                    pass

        if equity is None or len(equity) < 10:
            return err_actionable("could not find equity curve data in the latest run", tool="drawdown_analysis")

        equity = np.array(equity, dtype=float)

        # Compute drawdown series
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak

        # Find drawdown periods
        in_dd = drawdown < 0
        periods = []
        start = None
        for i in range(len(in_dd)):
            if in_dd[i] and start is None:
                start = i
            elif not in_dd[i] and start is not None:
                # Drawdown ended at i-1, recovered at i
                depth = float(np.min(drawdown[start:i]))
                trough_idx = start + int(np.argmin(drawdown[start:i]))
                periods.append({
                    "start_idx": int(start),
                    "trough_idx": int(trough_idx),
                    "recovery_idx": int(i),
                    "depth": round(depth, 4),
                    "duration": int(i - start),
                    "recovery_duration": int(i - trough_idx),
                })
                start = None

        # If still in drawdown at end
        if start is not None:
            depth = float(np.min(drawdown[start:]))
            trough_idx = start + int(np.argmin(drawdown[start:]))
            periods.append({
                "start_idx": int(start),
                "trough_idx": int(trough_idx),
                "recovery_idx": None,
                "depth": round(depth, 4),
                "duration": int(len(equity) - start),
                "recovery_duration": None,
                "note": "still in drawdown",
            })

        # Sort by depth and take top N
        periods.sort(key=lambda p: p["depth"])
        top_periods = periods[:top_n]

        max_dd = round(float(np.min(drawdown)), 4)
        current_dd = round(float(drawdown[-1]), 4)

        return _ok({
            "strategy": strategy_name,
            "run": latest_run.name,
            "equity_length": len(equity),
            "max_drawdown": max_dd,
            "current_drawdown": current_dd,
            "n_drawdown_periods": len(periods),
            "top_drawdowns": top_periods,
        })


# ── 18. BenchmarkComparison ────────────────────────────────────────────


class BenchmarkComparison(BaseTool):
    """策略 vs 基准表现对比（alpha/beta/IR）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 对比策略最新 run 的权益曲线与基准 (DuckDB ohlcv 中的指数/标的)
    # 的日收益: 年化 alpha、beta、跟踪误差、信息比率、最大相对回撤与
    # 双方年化收益。用于判断策略是否相对基准有超额。
    #
    # ## 参数
    # - strategy_name: 策略名 (必填)
    # - benchmark_code: 基准代码 (必填, 如 000300.SH, 须已在 ohlcv)
    # - start_date/end_date: 基准数据时间窗 (可选, ISO 日期)
    #
    # ## 示例
    # {"strategy_name": "mom_20d", "benchmark_code": "000300.SH"}
    #
    # ## 边界
    # 只读工具; 需要 workspace; 策略须有最新权益曲线 (≥10 点);
    # 基准代码须已入库; 两者按尾部对齐取较短长度; 基准查询用字符串
    # 拼接 asset 值 — 仅传已知代码。
    #
    # ## 错误处理范式
    # - 策略/基准缺参 → error + expected
    # - 基准未入库/无数据 → error, 先 get_market_data(benchmark_code)
    # - 权益曲线缺失 → error
    # - beta 分母为零时 beta/alpha 为 null (非失败)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: run_backtest + get_market_data; 同类: drawdown_analysis
    # ─────────────────────────────────────────────────────────────
    """

    name = "benchmark_comparison"
    description = "对比策略与基准: alpha/beta/tracking error/information ratio/相对回撤。"
    repeatable = True
    category = "回测"

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str,
        benchmark_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        import numpy as np

        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="benchmark_comparison")
        workspace = ctx.workspace
        if not strategy_name:
            return err_actionable("missing 'strategy_name'", tool="benchmark_comparison")
        if not benchmark_code:
            return err_actionable("missing 'benchmark_code'", tool="benchmark_comparison")

        # Get strategy equity from latest run
        runs_dir = workspace / "strategies" / strategy_name / "runs"
        if not runs_dir.exists():
            return err_actionable(f"runs directory not found: {runs_dir}", tool="benchmark_comparison")

        run_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()])
        if not run_dirs:
            return err_actionable("no runs found", tool="benchmark_comparison")

        latest_run = run_dirs[-1]

        import pandas as pd
        strategy_equity = None
        for fname in ["equity.csv", "equity_curve.csv", "portfolio.csv", "nav.csv"]:
            fpath = latest_run / fname
            if fpath.exists():
                try:
                    eq_df = pd.read_csv(fpath)
                    for col in ["equity", "nav", "portfolio_value", "value", "close"]:
                        if col in eq_df.columns:
                            strategy_equity = eq_df[col].values.astype(float)
                            break
                    if strategy_equity is not None:
                        break
                except Exception:
                    continue

        if strategy_equity is None or len(strategy_equity) < 10:
            return err_actionable("could not find strategy equity curve", tool="benchmark_comparison")

        # Get benchmark prices from DuckDB
        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:
            return err_actionable(f"db open failed: {exc}", tool="benchmark_comparison")
        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="benchmark_comparison")

        try:
            query = f"SELECT date, close FROM ohlcv WHERE asset = '{benchmark_code}'"
            if start_date:
                query += f" AND date >= '{start_date}'"
            if end_date:
                query += f" AND date <= '{end_date}'"
            query += " ORDER BY date"
            bench_df = conn.execute(query).fetch_df()
        except Exception as exc:
            return err_actionable(f"benchmark query failed: {exc}", tool="benchmark_comparison")

        if bench_df.empty:
            return err_actionable(f"no data found for benchmark '{benchmark_code}'", tool="benchmark_comparison")

        bench_equity = bench_df["close"].values.astype(float)

        # Align lengths
        min_len = min(len(strategy_equity), len(bench_equity))
        strat_ret = np.diff(strategy_equity[-min_len:]) / strategy_equity[-min_len:-1]
        bench_ret = np.diff(bench_equity[-min_len:]) / bench_equity[-min_len:-1]

        # Compute metrics
        excess_ret = strat_ret - bench_ret
        bench_var = float(np.var(bench_ret))
        beta = (float(np.cov(strat_ret, bench_ret, ddof=0)[0, 1] / bench_var)
                if bench_var > 0 else None)
        alpha_ann = float((np.mean(strat_ret) - beta * np.mean(bench_ret)) * 252) if beta is not None else None
        tracking_error = float(np.std(excess_ret) * np.sqrt(252))
        info_ratio = float(np.mean(excess_ret) * 252 / tracking_error) if tracking_error > 0 else None

        # Relative drawdown
        cum_excess = np.cumprod(1 + excess_ret)
        rel_peak = np.maximum.accumulate(cum_excess)
        rel_dd = (cum_excess - rel_peak) / rel_peak
        max_rel_dd = float(np.min(rel_dd))

        return _ok({
            "strategy": strategy_name,
            "benchmark": benchmark_code,
            "n_periods": min_len,
            "alpha_annualized": round(alpha_ann, 4) if alpha_ann is not None else None,
            "beta": round(beta, 4) if beta is not None else None,
            "tracking_error": round(tracking_error, 4),
            "information_ratio": round(info_ratio, 4) if info_ratio is not None else None,
            "max_relative_drawdown": round(max_rel_dd, 4),
            "strategy_annual_return": round(float(np.mean(strat_ret) * 252), 4),
            "benchmark_annual_return": round(float(np.mean(bench_ret) * 252), 4),
            "run": latest_run.name,
        })


# ── DataCleanTool ──────────────────────────────────────────────────


class DataCleanTool(BaseTool):
    """清洗工作区 OHLCV 数据（去重/填充/异常/变频）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.2.0
    # 变更: v1.2.0 迁移 v2 (显式签名 + ToolContext); v1.1.0 修复 price_data 查询
    #
    # ## 用途
    # 清洗策略的 OHLCV 数据: 去重/缺失填充/异常检测/变频。
    # preset 快捷执行或 steps+params 自定义。
    #
    # ## 参数
    # - strategy_name: 策略名 (默认 'default')
    # - preset: 预设模式 (quick/standard/thorough/resample/custom, 默认 standard)
    # - steps: 自定义步骤列表 (custom 模式)
    # - params: 自定义参数
    # - dry_run: 只生成报告不写库 (默认 True)
    #
    # ## 示例
    # {"strategy_name": "default", "preset": "standard", "dry_run": False}
    #
    # ## 边界
    # 写工具 (effects: db); dry_run=False 会清空并重写该策略的 price_data;
    # 幂等 (重跑安全)。
    #
    # ## 错误处理范式
    # - 无效 preset → error + expected 枚举
    # - 无 DB/空表 → error + fix 提示 get_market_data
    # - 均可安全重试 (dry_run=False 重跑覆盖)
    #
    # ## 相关工具
    # get_market_data: 数据来源; run_backtest: 清洗后回测
    # ─────────────────────────────────────────────
    """

    name = "clean_data"
    description = (
        "清洗 OHLCV 数据 (去重/缺失填充/异常检测/变频); preset 或 steps+params; "
        "dry_run 只出报告, False 写回 DuckDB。"
    )
    repeatable = True
    category = "数据"
    effects = frozenset({EFFECT_DB})

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str = "default",
        preset: str = "standard",
        steps: list[str] | None = None,
        params: dict[str, Any] | None = None,
        dry_run: bool = True,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="clean_data",
            )
        workspace = ctx.workspace

        # 验证 preset
        from ...tools.data_clean import PRESETS
        if preset not in PRESETS:
            return err_actionable(
                f"invalid preset: {preset}",
                received=preset,
                expected="one of: quick, standard, thorough, resample, custom",
                fix="use a valid preset name",
                tool="clean_data",
            )

        try:
            from ...tools.data_clean import clean_data

            # 加载数据
            from ...db import get_connection
            if not (workspace / "data.duckdb").exists():
                return err_actionable(
                    "no DuckDB database in workspace",
                    received={"strategy_name": strategy_name},
                    fix="use get_market_data to fetch and persist data first",
                    tool="clean_data",
                )
            conn = get_connection(workspace, read_only=dry_run)
            if conn is None:
                return err_actionable(
                    "failed to open DuckDB",
                    tool="clean_data",
                )

            df = conn.execute(
                "SELECT date, asset_code AS asset, open, high, low, close, volume "
                "FROM price_data WHERE strategy_name = ?",
                [strategy_name]
            ).fetch_df()

            if df.empty:
                conn.close()
                return err_actionable(
                    "ohlcv table is empty",
                    received={"strategy_name": strategy_name},
                    fix="use get_market_data to fetch data first",
                    tool="clean_data",
                )

            # 执行清洗
            result_df, report = clean_data(df, preset, steps, params, dry_run)

            # 如果不是 dry_run，保存结果
            if not dry_run:
                from ...db import save_ohlcv_to_db
                # 清空旧数据 (ohlcv 是视图不可 DELETE，直接清 price_data)
                conn.execute(
                    "DELETE FROM price_data WHERE strategy_name = ?",
                    [strategy_name]
                )
                # 保存新数据
                save_ohlcv_to_db(workspace, {strategy_name: result_df}, strategy_name)

            conn.close()

            return _ok({
                "strategy_name": strategy_name,
                "preset": preset,
                "dry_run": dry_run,
                "report": {
                    "initial_rows": report.initial_rows,
                    "final_rows": report.final_rows,
                    "steps_applied": report.steps_applied,
                    "duplicates_removed": report.duplicates_removed,
                    "missing_filled": report.missing_filled,
                    "outliers_detected": report.outliers_detected,
                    "resampled": report.resampled,
                    "original_freq": report.original_freq,
                    "target_freq": report.target_freq,
                    "params_applied": report.params_applied,
                },
                "message": report.message,
            })

        except Exception as exc:
            logger.exception("clean_data failed")
            return err_actionable(
                f"clean_data failed: {exc}",
                tool="clean_data",
            )


# ── Registry ─────────────────────────────────────────────────────────


# ── 19. ToolHelpTool ──────────────────────────────────────────────────
# 版本 1.0.0 | 变更: 初版, 返回 docstring 原文 + 元信息


class ToolHelpTool(BaseTool):
    """返回指定工具的详细版说明书（docstring 原文）。

    需要了解某个工具的详细用法、参数语义、边界或错误处理范式时调用；
    出错 debug 时调用可拿到该工具的完整错误处理说明。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 说明书移入 docstring (v2 范式 8 节模板)
    #
    # ## 用途
    # 返回注册表中任意工具的详细版说明书 (docstring 原文), 含用途、
    # 参数语义、示例、边界、错误处理范式、相关工具。系统提示中的
    # 工具目录 (简略版) 只提供一行摘要; 选中工具后或 debug 时用
    # 本工具获取完整说明。
    #
    # ## 参数
    # - name: 目标工具名 (与目录条目中的名字一致)
    #
    # ## 示例
    # {"name": "run_backtest"}
    #
    # ## 边界
    # 只读工具; 不修改任何状态; 需绑定 registry (由 build_default_registry
    # 构造)。
    #
    # ## 错误处理范式
    # - name 缺失/非字符串 → error + expected 提示
    # - 工具不存在 → error + available 列表 (最多 30 个)
    # - 未绑定 registry → error
    # - 本工具始终可安全重试
    #
    # ## 相关工具
    # list_skills / load_skill: 技能系统 (非工具) 的类似按需加载机制
    # ─────────────────────────────────────────────────────────────
    """

    # ── 工具说明书 ──────────────────────────────────────────────────
    # 版本: 1.0.0
    # 变更: 初版
    #
    # ## 用途
    # 返回注册表中任意工具的详细版说明书 (docstring 原文), 含用途、参数
    # 语义、示例、边界、错误处理范式、相关工具。系统提示中的工具目录
    # (简略版) 只提供一行摘要; 选中工具后或 debug 时用本工具获取完整说明。
    #
    # ## 参数
    # - name: 目标工具名 (与目录条目中的名字一致)
    #
    # ## 示例
    # {"name": "run_backtest"}
    #
    # ## 边界
    # 只读工具; 不修改任何状态。
    #
    # ## 错误处理范式
    # - name 缺失/非字符串 → error + expected 提示
    # - 工具不存在 → error + available 列表 (最多 30 个)
    # - 本工具始终可安全重试
    #
    # ## 相关工具
    # list_skills / load_skill: 技能系统 (非工具) 的类似按需加载机制
    # ─────────────────────────────────────────────────────────────

    name = "tool_help"
    description = (
        "返回指定工具的详细版说明书 (docstring 原文): 用途、参数语义、"
        "示例、边界、错误处理范式。选中工具后想知道具体用法或 debug 时调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "目标工具名。"},
        },
        "required": ["name"],
    }
    repeatable = True
    category = "技能"

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry

    def execute(
        self,
        ctx: ToolContext,
        name: str,
    ) -> str:
        if not isinstance(name, str) or not name:
            return err_actionable(
                "missing or invalid 'name'",
                received=name,
                expected="tool name, e.g. 'run_backtest'",
                fix="pass a tool name from the tool list in the system prompt",
                tool="tool_help",
            )
        if self._registry is None:
            return err_actionable(
                "tool_help is not bound to a registry",
                tool="tool_help",
            )
        tool = self._registry.get(name)
        if tool is None:
            available = sorted(self._registry.tool_names)[:30]
            return err_actionable(
                f"tool '{name}' not found",
                received=name,
                fix="use a name from the tool list in the system prompt",
                tool="tool_help",
                extra={"available": available},
            )
        return _ok({
            "name": tool.name,
            "category": tool.category,
            "brief": tool.brief,
            "doc": inspect.getdoc(tool) or "",
        })


def build_default_registry(workspace: Path | None = None) -> ToolRegistry:
    """Build a ToolRegistry with all tools.

    Tools are stateless; AgentLoop injects `workspace` per call.
    No workspace is bound at construction time.

    When ``workspace`` is given, composite tools from
    ``<workspace>/tools/combo/*.yml`` are loaded and registered
    (paradigm v2 分层注册: 显式核心 + 能力组 + 组合库加载器).
    """
    r = ToolRegistry()
    r.register(ReadFileTool())
    r.register(ListFilesTool())
    r.register(WriteFileTool())
    r.register(RunBacktestTool())
    r.register(ComputeFactorTool())
    r.register(GitDiffTool())
    r.register(ListHistoryTool())
    r.register(FactorAnalysisTool())
    r.register(PatternRecognitionTool())
    r.register(ListSkillsTool())
    r.register(LoadSkillTool())
    r.register(OptionsPricingTool())
    # Phase 4: Factor research tools
    r.register(FactorCrossSectionalAnalysis())
    r.register(FactorQuintileReturns())
    r.register(FactorICDecay())
    r.register(FactorTurnover())
    # Phase 4: Strategy analysis tools
    r.register(StrategyCompare())
    r.register(DrawdownAnalysis())
    r.register(BenchmarkComparison())
    # Phase 2: Web I/O tools (conditional on dependencies)
    try:
        from .web_tools import register_web_tools
        register_web_tools(r)
    except Exception:
        pass
    # Phase 3: Market data tools
    try:
        from .data_tools import register_data_tools
        register_data_tools(r)
    except Exception:
        pass
    # Goal management tools
    try:
        from .goal_tools import register_goal_tools
        register_goal_tools(r)
    except Exception:
        pass
    # Display tools (agent-driven right panel: show_chart / show_report)
    try:
        from .display_tools import register_display_tools
        register_display_tools(r)
    except Exception:
        pass
    # Data cleaning tools
    r.register(DataCleanTool())
    # Shell tools (opt-in, gated by allow_shell_tools)
    try:
        from .shell_tools import register_shell_tools
        register_shell_tools(r)
    except Exception:
        pass
    # Tool documentation (self-referential; registered last)
    r.register(ToolHelpTool(r))
    # Sub-agent delegation
    from .subagent_tool import SubAgentTool
    r.register(SubAgentTool())
    # Todo / task tracking
    from .todo_tools import TodoWriteTool
    r.register(TodoWriteTool())

    # Paradigm v2 分层注册: 组合库加载器 (workspace tools/combo/*.yml)
    if workspace is not None:
        from ..combo import load_combo_tools
        load_combo_tools(workspace, r)

    return r


__all__ = [
    "ReadFileTool",
    "WriteFileTool",
    "RunBacktestTool",
    "ComputeFactorTool",
    "GitDiffTool",
    "ListHistoryTool",
    "FactorAnalysisTool",
    "PatternRecognitionTool",
    "ListSkillsTool",
    "LoadSkillTool",
    "OptionsPricingTool",
    "FactorCrossSectionalAnalysis",
    "FactorQuintileReturns",
    "FactorICDecay",
    "FactorTurnover",
    "StrategyCompare",
    "DrawdownAnalysis",
    "BenchmarkComparison",
    "DataCleanTool",
    "ToolHelpTool",
    "CreateGoalTool",
    "AddEvidenceTool",
    "CompleteGoalTool",
    "GetGoalStatusTool",
    "ListGoalsTool",
    "build_default_registry",
]
