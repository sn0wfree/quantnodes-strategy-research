"""Tests for cli.py init flow + _render_template + cmd_evaluate.

覆盖 P0 修复：
- _render_template 占位符替换（不破坏 Python {} 字面量）
- cmd_init 端到端 workspace 创建（含 critic.md + 9 张 DuckDB 表）
- cmd_evaluate 端到端评估
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest


# ============================================================
# 1. _render_template 单元测试
# ============================================================

class TestRenderTemplate:
    """_render_template 占位符替换。"""

    def test_replaces_named_placeholder(self):
        from strategy_research.cli import _render_template
        result = _render_template("Hello {name}", name="world")
        assert result == "Hello world"

    def test_replaces_multiple_placeholders(self):
        from strategy_research.cli import _render_template
        result = _render_template(
            "{a} + {b} = {c}",
            a="1", b="2", c="3",
        )
        assert result == "1 + 2 = 3"

    def test_preserves_empty_dict_literal(self):
        """空 dict {} 不应被 .format() 误判为位置参数。"""
        from strategy_research.cli import _render_template
        template = "PARAMS = {}\nNAME = \"{name}\""
        result = _render_template(template, name="foo")
        assert "PARAMS = {}" in result
        assert 'NAME = "foo"' in result

    def test_preserves_dict_literal_with_contents(self):
        """带内容的 dict literal 也应保留。"""
        from strategy_research.cli import _render_template
        template = "DICT = {\"k\": \"v\", \"x\": \"{name}\"}"
        result = _render_template(template, name="foo")
        assert 'DICT = {"k": "v", "x": "foo"}' in result

    def test_preserves_brace_in_unrelated_text(self):
        """Markdown 中的 {} 也应保留（不替换为参数）。"""
        from strategy_research.cli import _render_template
        template = "# Title\n\nUse {cmd} to run.\nJSON: {not_a_param}"
        # 即使传 cmd 参数，也不应替换 {not_a_param}
        result = _render_template(template, cmd="python")
        assert "{not_a_param}" in result
        assert "python" not in result.split("JSON:")[1]  # JSON 后没有 cmd 替换

    def test_missing_kwargs_are_silently_ignored(self):
        """缺失 kwargs 不报错。"""
        from strategy_research.cli import _render_template
        # 应该不抛 KeyError
        result = _render_template("static text", unused="x")
        assert result == "static text"

    def test_idempotent_when_no_placeholders(self):
        """没有占位符时原样返回。"""
        from strategy_research.cli import _render_template
        text = "no placeholders here"
        assert _render_template(text, x="y") == text

    def test_multiple_occurrences_same_placeholder(self):
        """同一占位符出现多次应全部替换。"""
        from strategy_research.cli import _render_template
        result = _render_template(
            "{name}/{name}/{name}", name="foo"
        )
        assert result == "foo/foo/foo"

    def test_load_template_returns_string(self):
        """_load_template 应返回字符串（空字符串如果文件不存在）。"""
        from strategy_research.cli import _load_template
        result = _load_template("nonexistent.md")
        assert result == ""
        result = _load_template("strategy.py")
        assert isinstance(result, str)
        assert len(result) > 0


# ============================================================
# 2. cmd_init 端到端
# ============================================================

@pytest.fixture
def workspace_path(tmp_path: Path) -> Path:
    """返回 tmp workspace 路径。"""
    return tmp_path / "ws"


def _make_args(path: Path) -> argparse.Namespace:
    return argparse.Namespace(path=str(path), force=False)


class TestCmdInit:
    """cmd_init 端到端：创建 workspace、11 个 prompt、9 张 DuckDB 表。"""

    def test_creates_workspace_structure(
        self, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """workspace 结构：README/config.yaml/.prompts(11)/.skills(10)/strategies/<name>/data.duckdb/.git"""
        from strategy_research.cli import cmd_init

        # 提供 stdin 输入
        monkeypatch.setattr("builtins.input", lambda _: "test_strat")
        rc = cmd_init(_make_args(workspace_path))

        assert rc == 0
        assert (workspace_path / "README.md").exists()
        assert (workspace_path / "config.yaml").exists()
        assert (workspace_path / "data.duckdb").exists()
        assert (workspace_path / ".git").exists()

    def test_copies_all_11_prompts_including_critic(
        self, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """.prompts/ 应包含 11 个文件（含 critic.md）。"""
        from strategy_research.cli import cmd_init
        monkeypatch.setattr("builtins.input", lambda _: "test_strat")

        cmd_init(_make_args(workspace_path))

        prompts_dir = workspace_path / ".prompts"
        assert prompts_dir.exists()
        prompt_files = sorted(p.name for p in prompts_dir.iterdir() if p.suffix == ".md")
        assert len(prompt_files) == 11
        assert "critic.md" in prompt_files
        # 验证其他 10 个也存在
        for expected in [
            "orchestrator.md", "data_quality.md", "researcher.md",
            "factor_analyst.md", "strategist.md", "portfolio_construction.md",
            "risk_controller.md", "attribution_analyst.md",
            "anti_overfit_analyst.md", "backtest_diagnostics.md",
        ]:
            assert expected in prompt_files, f"{expected} missing"

    def test_copies_all_10_skills(
        self, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """.skills/ 应包含 10 个 markdown。"""
        from strategy_research.cli import cmd_init
        monkeypatch.setattr("builtins.input", lambda _: "test_strat")

        cmd_init(_make_args(workspace_path))

        skills_dir = workspace_path / ".skills"
        assert skills_dir.exists()
        skill_files = sorted(p.name for p in skills_dir.iterdir() if p.suffix == ".md")
        assert len(skill_files) == 10

    def test_creates_strategy_with_rendered_templates(
        self, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """strategy.py 应有正确的 strategy_name 替换 + 默认 momentum 因子。"""
        from strategy_research.cli import cmd_init

        # 模拟 cmd_init 的多次 input 调用：strategy_name / strategy_type / goal_metric
        inputs = iter(["momentum_strat", "rotation", "calmar"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        cmd_init(_make_args(workspace_path))

        strat_dir = workspace_path / "strategies" / "momentum_strat"
        assert strat_dir.exists()
        assert (strat_dir / "strategy.py").exists()
        assert (strat_dir / "prepare.py").exists()
        assert (strat_dir / "program.md").exists()

        # strategy.py 应包含替换后的 strategy_name
        strategy_content = (strat_dir / "strategy.py").read_text(encoding="utf-8")
        assert "momentum_strat" in strategy_content
        # 默认 momentum 因子
        assert "momentum_20_60" in strategy_content
        assert "ts_mean(close, 20) / ts_mean(close, 60) - 1" in strategy_content

        # prepare.py: GOAL_METRIC 应被替换为 calmar
        prepare_content = (strat_dir / "prepare.py").read_text(encoding="utf-8")
        assert 'GOAL_METRIC = "calmar"' in prepare_content
        # Python {} literal 不应被破坏
        assert "return {}" in prepare_content or "factors = {}" in prepare_content

    def test_duckdb_has_9_tables(
        self, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """DuckDB 应有 9 张表（完整 schema）。"""
        import duckdb
        from strategy_research.cli import cmd_init
        monkeypatch.setattr("builtins.input", lambda _: "test_strat")

        cmd_init(_make_args(workspace_path))

        conn = duckdb.connect(str(workspace_path / "data.duckdb"), read_only=True)
        tables = sorted(t[0] for t in conn.execute("SHOW TABLES").fetchall())
        conn.close()

        # 9 张表
        assert tables == [
            "backtest_results", "data_fingerprint", "factor_data",
            "factor_registry", "import_meta", "nav_history",
            "price_data", "validation_cache", "weight_history",
        ]

    def test_force_overwrites_existing_dir(
        self, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """--force 应允许非空目录。"""
        from strategy_research.cli import cmd_init
        workspace_path.mkdir(parents=True)
        (workspace_path / "existing_file.txt").write_text("old")

        monkeypatch.setattr("builtins.input", lambda _: "test_strat")
        args = argparse.Namespace(path=str(workspace_path), force=True)
        rc = cmd_init(args)
        assert rc == 0

    def test_aborts_if_dir_not_empty_without_force(
        self, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """目录非空且无 --force 应返回 1。"""
        from strategy_research.cli import cmd_init
        workspace_path.mkdir(parents=True)
        (workspace_path / "existing_file.txt").write_text("old")

        monkeypatch.setattr("builtins.input", lambda _: "test_strat")
        args = argparse.Namespace(path=str(workspace_path), force=False)
        rc = cmd_init(args)
        assert rc == 1


# ============================================================
# 3. cmd_evaluate 端到端
# ============================================================


class TestCmdEvaluate:
    """cmd_evaluate 端到端。"""

    def _init_workspace(self, workspace_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
        """辅助：先 init，然后返回 strategy_name。"""
        from strategy_research.cli import cmd_init
        strategy_name = "eval_strat"
        monkeypatch.setattr("builtins.input", lambda _: strategy_name)
        cmd_init(_make_args(workspace_path))
        return strategy_name

    def test_evaluate_runs_and_writes_metrics(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """cmd_evaluate 应跑一次回测并写 run_XXXX。"""
        from strategy_research.cli import cmd_evaluate

        workspace = tmp_path / "ws"
        strategy_name = self._init_workspace(workspace, monkeypatch)

        args = argparse.Namespace(
            path=str(workspace),
            strategy=strategy_name,
            description="unit test",
            timeout=60,
        )
        rc = cmd_evaluate(args)
        assert rc == 0

        # 应有 run_0001 (init 时跑了 baseline 是 run_0000)
        runs_dir = workspace / "strategies" / strategy_name / "runs"
        run_dirs = sorted(p.name for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("run_"))
        assert "run_0001" in run_dirs

    def test_evaluate_returns_metrics(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ):
        """cmd_evaluate 输出应包含 8 项 metrics。"""
        from strategy_research.cli import cmd_evaluate

        workspace = tmp_path / "ws"
        strategy_name = self._init_workspace(workspace, monkeypatch)

        args = argparse.Namespace(
            path=str(workspace),
            strategy=strategy_name,
            description="",
            timeout=60,
        )
        cmd_evaluate(args)

        captured = capsys.readouterr().out
        for metric in ["Calmar", "Sharpe", "MaxDD", "AnnRet", "AnnVol", "Sortino", "Turnover"]:
            assert metric in captured, f"{metric} not in output"

    def test_evaluate_fails_if_workspace_invalid(self, tmp_path: Path):
        """workspace 无效应返回 1。"""
        from strategy_research.cli import cmd_evaluate

        workspace = tmp_path / "nonexistent"
        args = argparse.Namespace(
            path=str(workspace),
            strategy=None,
            description="",
            timeout=60,
        )
        rc = cmd_evaluate(args)
        assert rc == 1