"""Alpha Zoo 因子库。

包含 5 个因子库:
- alpha101: Kakushadze 101 个公式化因子
- gtja191: 国泰君安 191 个 A 股截面因子
- qlib158: 微软 Qlib 158 个 ML 因子
- academic: 11 个学术因子 (Fama-French, Carhart 等)
- fundamental: 4 个基本面因子

加载优先级: YAML 优先，.py 作为 fallback。
"""

import importlib
from pathlib import Path
from typing import Optional


ALPHA_ZOOS = {
    "alpha101": "Kakushadze 101 公式化因子",
    "gtja191": "国泰君安 191 A 股截面因子",
    "qlib158": "微软 Qlib 158 ML 因子",
    "academic": "11 个学术因子",
    "fundamental": "4 个基本面因子",
}

_zoo_root = Path(__file__).parent


def _resolve_alpha_name(zoo_name: str, alpha_id: str) -> str:
    """解析 alpha_name，处理 alpha101_001 -> 001 的映射。"""
    # 去掉 zoo 前缀
    prefix = f"{zoo_name}_"
    if alpha_id.startswith(prefix):
        return alpha_id[len(prefix):]

    # 尝试直接用完整 ID
    return alpha_id


def _find_yaml_file(zoo_name: str, alpha_name: str) -> Optional[Path]:
    """查找 YAML 配置文件。"""
    zoo_dir = _zoo_root / zoo_name

    # 尝试直接匹配 (如 alpha_001.yaml)
    yaml_file = zoo_dir / f"{alpha_name}.yaml"
    if yaml_file.exists():
        return yaml_file

    # 尝试带 alpha_ 前缀的匹配 (如 alpha_001.yaml)
    yaml_file = zoo_dir / f"alpha_{alpha_name}.yaml"
    if yaml_file.exists():
        return yaml_file

    # 尝试带 zoo 前缀的匹配 (如 alpha101_001.yaml)
    yaml_file = zoo_dir / f"{zoo_name}_{alpha_name}.yaml"
    if yaml_file.exists():
        return yaml_file

    return None


def _find_py_file(zoo_name: str, alpha_name: str) -> Optional[Path]:
    """查找 .py 因子文件。"""
    zoo_dir = _zoo_root / zoo_name

    # 尝试直接匹配 (如 001.py)
    py_file = zoo_dir / f"{alpha_name}.py"
    if py_file.exists():
        return py_file

    # 尝试带 alpha_ 前缀的匹配 (如 alpha_001.py)
    py_file = zoo_dir / f"alpha_{alpha_name}.py"
    if py_file.exists():
        return py_file

    # 尝试带 zoo 前缀的匹配 (如 alpha101_001.py)
    py_file = zoo_dir / f"{zoo_name}_{alpha_name}.py"
    if py_file.exists():
        return py_file

    return None


def list_alphas(zoo: Optional[str] = None) -> list[dict]:
    """列出可用因子。

    同时列出 .py 和 .yaml 因子，去重后返回。
    """
    import json

    results = []
    seen = set()

    for zoo_name in ALPHA_ZOOS:
        if zoo and zoo_name != zoo:
            continue
        zoo_dir = _zoo_root / zoo_name

        # 列出 .yaml 因子
        for f in sorted(zoo_dir.glob("*.yaml")):
            alpha_id = f"{zoo_name}_{f.stem}"
            if alpha_id not in seen:
                results.append({
                    "id": alpha_id,
                    "zoo": zoo_name,
                    "file": str(f),
                    "format": "yaml",
                })
                seen.add(alpha_id)

        # 列出 .py 因子 (跳过已有 YAML 的)
        for f in sorted(zoo_dir.glob("*.py")):
            if f.name.startswith("_"):
                continue
            alpha_id = f"{zoo_name}_{f.stem}"
            if alpha_id not in seen:
                results.append({
                    "id": alpha_id,
                    "zoo": zoo_name,
                    "file": str(f),
                    "format": "py",
                })
                seen.add(alpha_id)

    return results


def compute_alpha(alpha_id: str, panel: dict) -> "pd.DataFrame":
    """计算单个因子。

    加载优先级: YAML 优先，.py 作为 fallback。
    """
    import pandas as pd
    import numpy as np

    parts = alpha_id.split("_", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid alpha_id: {alpha_id}")

    zoo_name, alpha_name = parts[0], parts[1]
    # Handle multi-digit IDs like alpha101_001
    if zoo_name not in ALPHA_ZOOS:
        # Try combining first part as zoo
        for z in ALPHA_ZOOS:
            if alpha_id.startswith(z + "_"):
                zoo_name = z
                alpha_name = alpha_id[len(z) + 1:]
                break

    # 优先加载 YAML
    yaml_file = _find_yaml_file(zoo_name, alpha_name)
    if yaml_file:
        try:
            from .alpha_zoo_yaml import load_alpha_yaml, compute_alpha_from_yaml
            config = load_alpha_yaml(yaml_file)
            result = compute_alpha_from_yaml(config, panel)

            # Validate output
            if not isinstance(result, pd.DataFrame):
                raise TypeError(f"Alpha {alpha_id} must return DataFrame, got {type(result)}")
            if result.shape != panel["close"].shape:
                raise ValueError(f"Shape mismatch: {result.shape} != {panel['close'].shape}")
            # 校验 inf 比例 — 允许 30% 以下（随机面板 ts_corr 会大量产出 inf）
            n_inf = int(np.isinf(result.values).sum())
            total = result.size
            if total > 0 and n_inf / total > 0.30:
                raise ValueError(f"Alpha {alpha_id}: {n_inf}/{total} inf values ({n_inf/total:.1%})")

            return result
        except Exception as e:
            # YAML 加载失败，尝试 fallback 到 .py
            import logging
            logging.getLogger(__name__).debug("YAML load failed for %s, trying .py fallback: %s", alpha_id, e)

    # Fallback: 加载 .py
    py_file = _find_py_file(zoo_name, alpha_name)
    if py_file:
        # 从文件路径提取模块名
        module_name = py_file.stem  # 如 "alpha_001"
        module_path = f"strategy_research.core.alpha_zoo.{zoo_name}.{module_name}"
        try:
            mod = importlib.import_module(module_path)
        except ImportError as e:
            raise ImportError(f"Cannot load alpha {alpha_id}: {e}")

        if not hasattr(mod, "compute"):
            raise AttributeError(f"Alpha {alpha_id} has no compute() function")

        result = mod.compute(panel)

        # Validate output
        if not isinstance(result, pd.DataFrame):
            raise TypeError(f"Alpha {alpha_id} must return DataFrame, got {type(result)}")
        if result.shape != panel["close"].shape:
            raise ValueError(f"Shape mismatch: {result.shape} != {panel['close'].shape}")
        n_inf = int(np.isinf(result.values).sum())
        total = result.size
        if total > 0 and n_inf / total > 0.30:
            raise ValueError(f"Alpha {alpha_id}: {n_inf}/{total} inf values ({n_inf/total:.1%})")

        return result

    raise FileNotFoundError(f"Alpha {alpha_id} not found (no .yaml or .py in {zoo_name}/)")
