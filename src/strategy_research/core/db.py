"""DuckDB 工具函数。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# ============================================================
# 连接管理
# ============================================================

def get_db_path(workspace_path: Path) -> Path:
    """获取 DuckDB 文件路径。"""
    return workspace_path / "data.duckdb"


def get_connection(workspace_path: Path, read_only: bool = False):
    """获取 DuckDB 连接。"""
    try:
        import duckdb
    except ImportError:
        print("⚠️  duckdb 未安装。安装: pip install duckdb")
        return None

    db_path = get_db_path(workspace_path)
    return duckdb.connect(str(db_path), read_only=read_only)


# ============================================================
# 初始化 SQL
# ============================================================

DUCKDB_INIT_SQL = """
-- 因子注册表
CREATE TABLE IF NOT EXISTS factor_registry (
    factor_name VARCHAR NOT NULL,
    factor_code VARCHAR NOT NULL,
    factor_type VARCHAR NOT NULL,
    category VARCHAR,
    source VARCHAR,
    lookback_window INTEGER,
    strategy_name VARCHAR NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_by VARCHAR,
    PRIMARY KEY (strategy_name, factor_name)
);

-- 验证缓存
CREATE TABLE IF NOT EXISTS validation_cache (
    factor_name VARCHAR NOT NULL,
    factor_code VARCHAR NOT NULL,
    strategy_name VARCHAR NOT NULL,
    ic_mean DOUBLE,
    ic_std DOUBLE,
    ir DOUBLE,
    ic_decay_1d DOUBLE,
    ic_decay_5d DOUBLE,
    ic_decay_20d DOUBLE,
    rank_ic_mean DOUBLE,
    stability_score DOUBLE,
    diversification_score DOUBLE,
    turnover_score DOUBLE,
    monotonicity_score DOUBLE,
    coverage_score DOUBLE,
    overall_score DOUBLE,
    is_valid BOOLEAN,
    fail_reasons VARCHAR,
    validated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source VARCHAR,
    data_fingerprint VARCHAR,
    PRIMARY KEY (strategy_name, factor_name)
);

-- 回测结果
CREATE TABLE IF NOT EXISTS backtest_results (
    strategy_name VARCHAR NOT NULL,
    run VARCHAR NOT NULL,
    commit_hash VARCHAR,
    action VARCHAR,
    goal_metric DOUBLE,
    calmar DOUBLE,
    sharpe DOUBLE,
    max_dd DOUBLE,
    ann_return DOUBLE,
    ann_vol DOUBLE,
    sortino DOUBLE,
    turnover DOUBLE,
    factors_added INTEGER DEFAULT 0,
    factors_removed INTEGER DEFAULT 0,
    params_changed INTEGER DEFAULT 0,
    status VARCHAR,
    description VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (strategy_name, run)
);

-- 数据指纹
CREATE TABLE IF NOT EXISTS data_fingerprint (
    table_name VARCHAR NOT NULL,
    strategy_name VARCHAR NOT NULL,
    fingerprint VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_count INTEGER,
    PRIMARY KEY (table_name, strategy_name)
);
"""


def init_db(workspace_path: Path) -> bool:
    """初始化 DuckDB 表结构。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        conn.execute(DUCKDB_INIT_SQL)
        conn.close()
        return True
    except Exception as e:
        print(f"❌ DuckDB 初始化失败: {e}")
        conn.close()
        return False


# ============================================================
# 因子注册表操作
# ============================================================

def register_factor(
    workspace_path: Path,
    strategy_name: str,
    factor_name: str,
    factor_code: str,
    factor_type: str = "asset_ts",
    category: str = "",
    source: str = "",
    lookback_window: int = 20,
) -> bool:
    """注册因子到因子注册表。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        conn.execute("""
            INSERT OR REPLACE INTO factor_registry
            (factor_name, factor_code, factor_type, category, source, lookback_window, strategy_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [factor_name, factor_code, factor_type, category, source, lookback_window, strategy_name])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 注册因子失败: {e}")
        conn.close()
        return False


def get_factors(workspace_path: Path, strategy_name: str) -> list[dict]:
    """获取策略的所有因子。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return []

    try:
        result = conn.execute("""
            SELECT factor_name, factor_code, factor_type, category, source, lookback_window
            FROM factor_registry
            WHERE strategy_name = ?
            ORDER BY added_at
        """, [strategy_name]).fetchall()
        conn.close()

        return [
            {
                "factor_name": row[0],
                "factor_code": row[1],
                "factor_type": row[2],
                "category": row[3],
                "source": row[4],
                "lookback_window": row[5],
            }
            for row in result
        ]
    except Exception as e:
        print(f"❌ 获取因子失败: {e}")
        conn.close()
        return []


def remove_factor(workspace_path: Path, strategy_name: str, factor_name: str) -> bool:
    """从因子注册表移除因子。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        conn.execute("""
            DELETE FROM factor_registry
            WHERE strategy_name = ? AND factor_name = ?
        """, [strategy_name, factor_name])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 移除因子失败: {e}")
        conn.close()
        return False


# ============================================================
# 验证缓存操作
# ============================================================

def cache_validation(
    workspace_path: Path,
    strategy_name: str,
    factor_name: str,
    factor_code: str,
    ic_mean: float,
    ic_std: float,
    ir: float,
    rank_ic_mean: float = 0.0,
    ic_decay_1d: float = 0.0,
    ic_decay_5d: float = 0.0,
    ic_decay_20d: float = 0.0,
    scores: Optional[dict] = None,
    overall_score: float = 0.0,
    is_valid: bool = False,
    fail_reasons: str = "",
    source: str = "",
    data_fingerprint: str = "",
) -> bool:
    """缓存因子验证结果。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    scores = scores or {}
    try:
        conn.execute("""
            INSERT OR REPLACE INTO validation_cache
            (factor_name, factor_code, strategy_name, ic_mean, ic_std, ir,
             rank_ic_mean, ic_decay_1d, ic_decay_5d, ic_decay_20d,
             stability_score, diversification_score, turnover_score,
             monotonicity_score, coverage_score, overall_score,
             is_valid, fail_reasons, source, data_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            factor_name, factor_code, strategy_name,
            ic_mean, ic_std, ir, rank_ic_mean,
            ic_decay_1d, ic_decay_5d, ic_decay_20d,
            scores.get("stability", 0.0),
            scores.get("diversification", 0.0),
            scores.get("turnover", 0.0),
            scores.get("monotonicity", 0.0),
            scores.get("coverage", 0.0),
            overall_score,
            is_valid, fail_reasons, source, data_fingerprint,
        ])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 缓存验证结果失败: {e}")
        conn.close()
        return False


def get_validation_cache(workspace_path: Path, strategy_name: str) -> list[dict]:
    """获取验证缓存。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return []

    try:
        result = conn.execute("""
            SELECT factor_name, factor_code, ic_mean, ic_std, ir,
                   rank_ic_mean, overall_score, is_valid, fail_reasons
            FROM validation_cache
            WHERE strategy_name = ?
            ORDER BY validated_at DESC
        """, [strategy_name]).fetchall()
        conn.close()

        return [
            {
                "factor_name": row[0],
                "factor_code": row[1],
                "ic_mean": row[2],
                "ic_std": row[3],
                "ir": row[4],
                "rank_ic_mean": row[5],
                "overall_score": row[6],
                "is_valid": row[7],
                "fail_reasons": row[8],
            }
            for row in result
        ]
    except Exception as e:
        print(f"❌ 获取验证缓存失败: {e}")
        conn.close()
        return []


# ============================================================
# 回测结果操作
# ============================================================

def save_backtest_result(
    workspace_path: Path,
    strategy_name: str,
    run: str,
    commit_hash: str = "",
    action: str = "",
    goal_metric: float = 0.0,
    calmar: float = 0.0,
    sharpe: float = 0.0,
    max_dd: float = 0.0,
    ann_return: float = 0.0,
    ann_vol: float = 0.0,
    sortino: float = 0.0,
    turnover: float = 0.0,
    factors_added: int = 0,
    factors_removed: int = 0,
    params_changed: int = 0,
    status: str = "pending",
    description: str = "",
) -> bool:
    """保存回测结果到 DuckDB。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        conn.execute("""
            INSERT OR REPLACE INTO backtest_results
            (strategy_name, run, commit_hash, action, goal_metric,
             calmar, sharpe, max_dd, ann_return, ann_vol, sortino, turnover,
             factors_added, factors_removed, params_changed, status, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            strategy_name, run, commit_hash, action, goal_metric,
            calmar, sharpe, max_dd, ann_return, ann_vol, sortino, turnover,
            factors_added, factors_removed, params_changed, status, description,
        ])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 保存回测结果失败: {e}")
        conn.close()
        return False


def get_backtest_results(
    workspace_path: Path,
    strategy_name: str,
    limit: int = 100,
) -> list[dict]:
    """获取回测结果。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return []

    try:
        result = conn.execute("""
            SELECT run, commit_hash, action, goal_metric, calmar, sharpe,
                   max_dd, ann_return, ann_vol, sortino, turnover, status, description
            FROM backtest_results
            WHERE strategy_name = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, [strategy_name, limit]).fetchall()
        conn.close()

        return [
            {
                "run": row[0],
                "commit": row[1],
                "action": row[2],
                "goal_metric": row[3],
                "calmar": row[4],
                "sharpe": row[5],
                "max_dd": row[6],
                "ann_return": row[7],
                "ann_vol": row[8],
                "sortino": row[9],
                "turnover": row[10],
                "status": row[11],
                "description": row[12],
            }
            for row in result
        ]
    except Exception as e:
        print(f"❌ 获取回测结果失败: {e}")
        conn.close()
        return []


def get_best_backtest(workspace_path: Path, strategy_name: str) -> Optional[dict]:
    """获取最佳回测结果。"""
    results = get_backtest_results(workspace_path, strategy_name)
    if not results:
        return None

    # 按 goal_metric 排序 (假设 maximize)
    valid = [r for r in results if r["status"] == "keep"]
    if not valid:
        return None

    return max(valid, key=lambda x: x.get("goal_metric", 0))


# ============================================================
# 数据指纹操作
# ============================================================

def update_data_fingerprint(
    workspace_path: Path,
    strategy_name: str,
    table_name: str,
    fingerprint: str,
    row_count: int = 0,
) -> bool:
    """更新数据指纹。"""
    conn = get_connection(workspace_path)
    if conn is None:
        return False

    try:
        conn.execute("""
            INSERT OR REPLACE INTO data_fingerprint
            (table_name, strategy_name, fingerprint, row_count)
            VALUES (?, ?, ?, ?)
        """, [table_name, strategy_name, fingerprint, row_count])
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 更新数据指纹失败: {e}")
        conn.close()
        return False


def get_data_fingerprint(workspace_path: Path, strategy_name: str) -> Optional[dict]:
    """获取数据指纹。"""
    conn = get_connection(workspace_path, read_only=True)
    if conn is None:
        return None

    try:
        result = conn.execute("""
            SELECT table_name, fingerprint, row_count, updated_at
            FROM data_fingerprint
            WHERE strategy_name = ?
            ORDER BY updated_at DESC
            LIMIT 1
        """, [strategy_name]).fetchone()
        conn.close()

        if result:
            return {
                "table_name": result[0],
                "fingerprint": result[1],
                "row_count": result[2],
                "updated_at": result[3],
            }
        return None
    except Exception as e:
        print(f"❌ 获取数据指纹失败: {e}")
        conn.close()
        return None
