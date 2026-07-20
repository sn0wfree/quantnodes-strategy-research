"""DuckDB 工具函数。"""
from __future__ import annotations

from pathlib import Path


def get_db_path(workspace_path: Path) -> Path:
    """获取 DuckDB 文件路径。"""
    return workspace_path / "data.duckdb"


def init_db(workspace_path: Path) -> None:
    """初始化 DuckDB 表结构。"""
    try:
        import duckdb
    except ImportError:
        print("⚠️  duckdb 未安装，跳过 DuckDB 初始化。")
        return

    db_path = get_db_path(workspace_path)
    conn = duckdb.connect(str(db_path))
    
    # TODO: 从文件加载 SQL
    conn.execute("""
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
        )
    """)
    
    conn.close()
