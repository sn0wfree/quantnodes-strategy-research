"""Alpha Zoo 适配器。

将 Alpha Zoo 的宽 DataFrame 格式适配到 strategy-research 的 Series 格式。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


class AlphaZooAdapter:
    """Alpha Zoo 适配器，提供统一接口访问 465+ 因子。"""

    def __init__(self):
        self._zoo_root = Path(__file__).parent / "alpha_zoo"

    def list_alphas(
        self,
        zoo: Optional[str] = None,
        theme: Optional[str] = None,
        universe: Optional[str] = None,
    ) -> list[dict]:
        """列出可用因子。

        Args:
            zoo: 因子库名称 (alpha101/gtja191/qlib158/academic/fundamental)
            theme: 主题过滤 (momentum/reversal/volatility/volume/...)
            universe: 市场过滤 (equity_cn/equity_us/...)

        Returns:
            list: [{"id": str, "zoo": str, "theme": list, "columns_required": list}]
        """
        from .alpha_zoo import ALPHA_ZOOS

        results = []
        for zoo_name in ALPHA_ZOOS:
            if zoo and zoo_name != zoo:
                continue
            zoo_dir = self._zoo_root / zoo_name
            for f in sorted(zoo_dir.glob("*.py")):
                if f.name.startswith("_") or f.name == "__init__.py":
                    continue
                alpha_id = f"{zoo_name}_{f.stem}"
                meta = self._load_meta(f)
                if meta:
                    # Apply filters
                    if theme and theme not in meta.get("theme", []):
                        continue
                    if universe and universe not in meta.get("universe", []):
                        continue
                    results.append({
                        "id": alpha_id,
                        "zoo": zoo_name,
                        "meta": meta,
                    })
                else:
                    results.append({
                        "id": alpha_id,
                        "zoo": zoo_name,
                        "meta": {},
                    })
        return results

    def get_alpha(self, alpha_id: str) -> dict:
        """获取单个因子的元数据。"""
        zoo_name, alpha_name = self._parse_id(alpha_id)
        py_file = self._zoo_root / zoo_name / f"{alpha_name}.py"
        if not py_file.exists():
            raise KeyError(f"Alpha not found: {alpha_id}")
        meta = self._load_meta(py_file) or {}
        return {"id": alpha_id, "zoo": zoo_name, "meta": meta, "file": str(py_file)}

    def compute_as_wide(
        self,
        alpha_id: str,
        prices: pd.DataFrame,
        volume: Optional[pd.DataFrame] = None,
        open_: Optional[pd.DataFrame] = None,
        high: Optional[pd.DataFrame] = None,
        low: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """计算因子值，返回宽 DataFrame (index=date, columns=assets)。

        Args:
            alpha_id: 因子 ID (如 "gtja191_001")
            prices: 收盘价 (index=date, columns=assets)
            volume: 成交量 (可选)
            open_: 开盘价 (可选)
            high: 最高价 (可选)
            low: 最低价 (可选)

        Returns:
            pd.DataFrame: 因子值 (同 prices 形状)
        """
        panel = {"close": prices}
        if volume is not None:
            panel["volume"] = volume
        if open_ is not None:
            panel["open"] = open_
        if high is not None:
            panel["high"] = high
        if low is not None:
            panel["low"] = low

        # Fill missing OHLCV with close
        for col in ["open", "high", "low", "volume"]:
            if col not in panel:
                panel[col] = prices if col != "volume" else pd.DataFrame(
                    0, index=prices.index, columns=prices.columns
                )

        # Auto-fill derived columns that many alphas need
        if "vwap" not in panel:
            panel["vwap"] = (panel["high"] + panel["low"] + panel["close"]) / 3.0
        if "amount" not in panel:
            panel["amount"] = panel["volume"] * panel["close"]
        if "returns" not in panel:
            panel["returns"] = panel["close"].pct_change().fillna(0)
        for w in [5, 10, 15, 20, 30, 50, 60]:
            key = f"adv{w}"
            if key not in panel:
                panel[key] = panel["volume"].rolling(w).mean().fillna(panel["volume"].mean())

        zoo_name, alpha_name = self._parse_id(alpha_id)
        module_path = f"strategy_research.core.alpha_zoo.{zoo_name}.{alpha_name}"
        mod = importlib.import_module(module_path)

        if not hasattr(mod, "compute"):
            raise AttributeError(f"Alpha {alpha_id} has no compute() function")

        result = mod.compute(panel)

        # Validate
        if not isinstance(result, pd.DataFrame):
            raise TypeError(f"Alpha {alpha_id} must return DataFrame, got {type(result)}")
        if result.shape != prices.shape:
            raise ValueError(f"Shape mismatch: {result.shape} != {prices.shape}")
        if np.any(np.isinf(result.values)):
            raise ValueError(f"Alpha {alpha_id} contains inf values")

        return result

    def compute_as_series(
        self,
        alpha_id: str,
        prices: pd.DataFrame,
        **kwargs,
    ) -> pd.Series:
        """计算因子值，返回 Series (MultiIndex: date, asset)。"""
        wide = self.compute_as_wide(alpha_id, prices, **kwargs)
        return wide.stack()

    def compute_batch(
        self,
        alpha_ids: list[str],
        prices: pd.DataFrame,
        **kwargs,
    ) -> pd.DataFrame:
        """批量计算因子，返回宽 DataFrame (columns=alpha_ids)。"""
        results = {}
        for aid in alpha_ids:
            try:
                results[aid] = self.compute_as_series(aid, prices, **kwargs)
            except Exception as e:
                print(f"⚠️  因子 {aid} 计算失败: {e}")
        return pd.DataFrame(results)

    def health(self) -> dict:
        """检查因子库健康状态。"""
        loaded = 0
        failed = 0
        errors = []
        for zoo_name in ["alpha101", "gtja191", "qlib158", "academic", "fundamental"]:
            zoo_dir = self._zoo_root / zoo_name
            for f in sorted(zoo_dir.glob("*.py")):
                if f.name.startswith("_") or f.name == "__init__.py":
                    continue
                alpha_id = f"{zoo_name}_{f.stem}"
                try:
                    meta = self._load_meta(f)
                    if meta:
                        loaded += 1
                    else:
                        failed += 1
                        errors.append(f"{alpha_id}: no __alpha_meta__")
                except Exception as e:
                    failed += 1
                    errors.append(f"{alpha_id}: {e}")
        return {"loaded": loaded, "failed": failed, "errors": errors[:20]}

    # ---- Internal helpers ----

    def _parse_id(self, alpha_id: str) -> tuple[str, str]:
        """解析 alpha_id 为 (zoo_name, alpha_name)。"""
        from .alpha_zoo import ALPHA_ZOOS
        for zoo_name in ALPHA_ZOOS:
            if alpha_id.startswith(zoo_name + "_"):
                alpha_name = alpha_id[len(zoo_name) + 1:]
                return zoo_name, alpha_name
        raise KeyError(f"Unknown zoo in alpha_id: {alpha_id}")

    def _load_meta(self, py_file: Path) -> Optional[dict]:
        """从 .py 文件中提取 __alpha_meta__ (AST 解析，不 import)。"""
        import ast
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "__alpha_meta__":
                            if isinstance(node.value, ast.Dict):
                                meta = {}
                                for key, value in zip(node.value.keys, node.value.values):
                                    if isinstance(key, ast.Constant):
                                        k = key.value
                                        if isinstance(value, ast.Constant):
                                            meta[k] = value.value
                                        elif isinstance(value, ast.List):
                                            meta[k] = [
                                                e.value for e in value.elts
                                                if isinstance(e, ast.Constant)
                                            ]
                                return meta
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Failed to extract AST metadata: %s", e)
        return None
