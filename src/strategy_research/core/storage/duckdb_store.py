"""DuckDBDataStore — default ``DataStore`` provider.

Wraps the existing ``core.db.py`` module-level functions so every call
site that already does ``from .db import save_ohlcv_to_db`` keeps working
unmodified. The wrapper is intentionally a no-op layer — each method
just delegates to the legacy function — so the 195+ existing tests
remain the equivalence oracle.

Why a wrapper instead of rewriting ``db.py``:
- The legacy module is 1165 lines with 30+ call sites. A wholesale
  rewrite would explode this PR's blast radius.
- v0.1 goal: stand up the Protocol + a working provider. Future phases
  can migrate individual methods to native SQL once we have DI tooling
  (P0-2.D) and tests for non-DuckDB backends.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pandas as pd

from ...core import db as _db


class DuckDBDataStore:
    """Default DataStore that delegates to ``core.db`` functions.

    All methods take ``workspace`` as the first positional argument (the
    Protocol's contract). ``init_db`` runs lazily on first write; reads
    no-op if the workspace is uninitialised.
    """

    # Lifecycle
    def init(self, workspace: Path) -> None:
        _db.init_db(workspace)

    def get_connection(self, workspace: Path, *, read_only: bool = False) -> Any:
        return _db.get_connection(workspace, read_only=read_only)

    # Price data
    def save_ohlcv(
        self,
        workspace: Path,
        data_map: dict[str, pd.DataFrame],
        *,
        strategy_name: str = "default",
    ) -> int:
        return _db.save_ohlcv_to_db(
            workspace,
            data_map,
            strategy_name=strategy_name,
        )

    def load_price_data(
        self,
        workspace: Path,
        strategy_name: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return _db.load_price_data(
            workspace,
            strategy_name,
            start_date=start_date,
            end_date=end_date,
        )

    def load_ohlcv_data(
        self,
        workspace: Path,
        strategy_name: str,
        *,
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        # ``load_ohlcv_data`` legacy signature is positional + uses
        # ``codes`` as the third arg (after start_date). Pass through
        # so callers that migrated to the Protocol get the same shape.
        return _db.load_ohlcv_data(
            workspace,
            strategy_name,
            codes=codes,
            start_date=start_date,
            end_date=end_date,
        )

    # Factor data + registry
    def save_factor_data(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        values: pd.DataFrame,
    ) -> bool:
        return _db.save_factor_data(
            workspace,
            strategy_name=strategy_name,
            factor_name=factor_name,
            values=values,
        )

    def load_factor_data(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return _db.load_factor_data(
            workspace,
            strategy_name=strategy_name,
            factor_name=factor_name,
            start_date=start_date,
            end_date=end_date,
        )

    def register_factor(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        formula: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> bool:
        # Legacy signature has ``factor_code`` (the formula body) plus
        # ``source``; ``description`` and ``tags`` are not in the legacy
        # schema, so we fold them into ``source`` for parity.
        legacy_source = description
        if tags:
            legacy_source = (legacy_source + " " if legacy_source else "") + \
                "tags:" + ",".join(tags)
        return _db.register_factor(
            workspace,
            strategy_name=strategy_name,
            factor_name=factor_name,
            factor_code=formula,
            source=legacy_source,
        )

    def list_factors(
        self,
        workspace: Path,
        *,
        strategy_name: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        # The legacy ``get_factors`` only filters by strategy_name and
        # has no ``tags`` column; ``register_factor`` encodes tags into
        # ``source`` as ``tags:t1,t2`` (parse that here).
        result = (
            _db.get_factors(workspace, strategy_name)
            if strategy_name
            else _db.get_factors(workspace, "")
        )
        if tags:
            tag_set = set(tags)
            filtered: list[dict] = []
            for f in result:
                source = f.get("source", "")
                parsed_tags: set[str] = set()
                for token in source.split():
                    if token.startswith("tags:"):
                        parsed_tags = set(token[len("tags:"):].split(","))
                        break
                if tag_set.issubset(parsed_tags):
                    filtered.append(f)
            result = filtered
        return result

    def remove_factor(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
    ) -> bool:
        return _db.remove_factor(workspace, strategy_name, factor_name)

    # Backtest results
    def save_backtest_result(
        self,
        workspace: Path,
        *,
        run_id: str,
        strategy_name: str,
        result: dict,
    ) -> bool:
        return _db.save_backtest_result(
            workspace,
            run_id=run_id,
            strategy_name=strategy_name,
            result=result,
        )

    def list_backtest_results(
        self,
        workspace: Path,
        *,
        strategy_name: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        results = _db.get_backtest_results(workspace, strategy_name, status)
        if limit is not None:
            results = results[:limit]
        return results

    def get_backtest_result(
        self,
        workspace: Path,
        run_id: str,
    ) -> Optional[dict]:
        # Legacy ``get_best_backtest`` is per-strategy; lookup-by-run_id
        # is approximated by scanning list_backtest_results. When this
        # becomes a hot path, add a dedicated ``get_backtest_result``
        # function to ``db.py``.
        for r in self.list_backtest_results(workspace):
            if r.get("run_id") == run_id:
                return r
        return None

    # Weight / NAV history
    def save_weight_history(
        self,
        workspace: Path,
        *,
        run_id: str,
        weights: pd.DataFrame,
    ) -> bool:
        return _db.save_weight_history(workspace, run_id=run_id, weights=weights)

    def load_weight_history(
        self,
        workspace: Path,
        run_id: str,
    ) -> pd.DataFrame:
        return _db.load_weight_history(workspace, run_id)

    def save_nav_history(
        self,
        workspace: Path,
        *,
        run_id: str,
        nav: pd.Series,
    ) -> bool:
        return _db.save_nav_history(workspace, run_id=run_id, nav=nav)

    def load_nav_history(
        self,
        workspace: Path,
        run_id: str,
    ) -> pd.Series:
        return _db.load_nav_history(workspace, run_id)

    # Validation cache
    def cache_validation(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        metrics: dict,
        ttl_seconds: int = 7 * 24 * 3600,
    ) -> bool:
        return _db.cache_validation(
            workspace,
            strategy_name=strategy_name,
            factor_name=factor_name,
            metrics=metrics,
            ttl_seconds=ttl_seconds,
        )

    def list_validation_cache(
        self,
        workspace: Path,
        *,
        strategy_name: str | None = None,
        factor_name: str | None = None,
    ) -> list[dict]:
        if strategy_name is None:
            return []
        return _db.get_validation_cache(workspace, strategy_name, factor_name)

    # Import metadata + fingerprint
    def get_last_import_date(
        self,
        workspace: Path,
        *,
        codes: list[str] | None = None,
    ) -> Optional[float]:
        return _db.get_last_import_date(workspace, codes=codes)

    def update_import_meta(
        self,
        workspace: Path,
        *,
        codes: list[str],
        import_date: float,
        source: str = "",
        row_count: int = 0,
    ) -> bool:
        return _db.update_import_meta(
            workspace,
            codes=codes,
            import_date=import_date,
            source=source,
            row_count=row_count,
        )

    def update_data_fingerprint(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        fingerprint: dict,
    ) -> bool:
        return _db.update_data_fingerprint(
            workspace,
            strategy_name=strategy_name,
            fingerprint=fingerprint,
        )

    def get_data_fingerprint(
        self,
        workspace: Path,
        strategy_name: str,
    ) -> Optional[dict]:
        return _db.get_data_fingerprint(workspace, strategy_name)


__all__ = ["DuckDBDataStore"]
