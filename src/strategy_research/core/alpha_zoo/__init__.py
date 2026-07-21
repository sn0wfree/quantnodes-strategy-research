"""Alpha Zoo 因子库。

包含 5 个因子库:
- alpha101: Kakushadze 101 个公式化因子
- gtja191: 国泰君安 191 个 A 股截面因子
- qlib158: 微软 Qlib 158 个 ML 因子
- academic: 11 个学术因子 (Fama-French, Carhart 等)
- fundamental: 4 个基本面因子
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


def list_alphas(zoo: Optional[str] = None) -> list[dict]:
    """列出可用因子。"""
    results = []
    for zoo_name in ALPHA_ZOOS:
        if zoo and zoo_name != zoo:
            continue
        zoo_dir = _zoo_root / zoo_name
        for f in sorted(zoo_dir.glob("*.py")):
            if f.name.startswith("_"):
                continue
            results.append({
                "id": f"{zoo_name}_{f.stem}",
                "zoo": zoo_name,
                "file": str(f),
            })
    return results


def compute_alpha(alpha_id: str, panel: dict) -> "pd.DataFrame":
    """计算单个因子。"""
    import pandas as pd

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

    module_path = f"strategy_research.core.alpha_zoo.{zoo_name}.{alpha_name}"
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

    import numpy as np
    if np.any(np.isinf(result.values)):
        raise ValueError(f"Alpha {alpha_id} contains inf values")

    return result
