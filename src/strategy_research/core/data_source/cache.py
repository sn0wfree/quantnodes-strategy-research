"""Content-addressed loader cache for market data.

Stores fetched DataFrames as parquet files keyed by SHA-256 hash of
(source, symbol, timeframe, start_date, end_date). Provides fetch-through
semantics: check cache first, fetch on miss, write back to cache.

Cache location: ~/.quantnodes-research/loader_cache/
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path.home() / ".quantnodes-research" / "loader_cache"
_CACHE_MAX_AGE_DAYS = 7  # entries older than this are stale


def _cache_root() -> Path:
    """Return the cache directory, creating it if needed."""
    env = os.environ.get("STRATEGY_RESEARCH_CACHE_DIR")
    root = Path(env) if env else _DEFAULT_CACHE_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_cache_key(
    source: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
) -> str:
    """Generate a deterministic SHA-256 cache key from request parameters."""
    content = f"{source}:{symbol}:{timeframe}:{start_date}:{end_date}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _cache_path(key: str) -> Path:
    """Return the parquet file path for a cache key."""
    return _cache_root() / f"{key}.parquet"


def cache_get(key: str) -> Optional[pd.DataFrame]:
    """Read a cached DataFrame by key. Returns None on miss or expiry."""
    path = _cache_path(key)
    if not path.exists():
        return None

    # Check age
    age_days = (time.time() - path.stat().st_mtime) / 86400
    if age_days > _CACHE_MAX_AGE_DAYS:
        logger.debug("cache expired: %s (%.1f days old)", key, age_days)
        return None

    try:
        df = pd.read_parquet(path)
        logger.debug("cache hit: %s (%d rows)", key, len(df))
        return df
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache read failed for %s: %s", key, exc)
        return None


def cache_put(key: str, df: pd.DataFrame) -> None:
    """Write a DataFrame to the cache atomically."""
    if df.empty:
        return

    path = _cache_path(key)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=_cache_root(), suffix=".tmp")
    try:
        os.close(tmp_fd)
        df.to_parquet(tmp_path, index=True)
        os.replace(tmp_path, path)
        logger.debug("cache put: %s (%d rows)", key, len(df))
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache write failed for %s: %s", key, exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def cached_fetch(
    fetch_fn,
    source: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    *,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch-through cache: check cache, fetch on miss, write back.

    Args:
        fetch_fn: Callable that takes (codes, start_date, end_date, interval, fields)
                  and returns dict[str, pd.DataFrame]. We call it with a single code.
        source: Data source name.
        symbol: Asset symbol.
        timeframe: K-line interval.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        force_refresh: Skip cache read, always fetch fresh.

    Returns:
        DataFrame for the requested symbol.
    """
    key = make_cache_key(source, symbol, timeframe, start_date, end_date)

    if not force_refresh:
        cached = cache_get(key)
        if cached is not None:
            return cached

    # Fetch fresh data
    try:
        result_map = fetch_fn([symbol], start_date, end_date, interval=timeframe)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch failed for %s/%s: %s", source, symbol, exc)
        raise

    df = result_map.get(symbol)
    if df is None or df.empty:
        logger.debug("no data returned for %s/%s", source, symbol)
        # Cache empty result briefly (1 day) to avoid hammering APIs
        return pd.DataFrame()

    # Write to cache
    cache_put(key, df)
    return df


def cache_stats() -> dict:
    """Return cache statistics."""
    root = _cache_root()
    files = list(root.glob("*.parquet"))
    total_bytes = sum(f.stat().st_size for f in files)
    return {
        "cache_dir": str(root),
        "entries": len(files),
        "total_size_mb": round(total_bytes / 1024 / 1024, 2),
    }


def cache_clear() -> int:
    """Delete all cache entries. Returns number of files removed."""
    root = _cache_root()
    count = 0
    for f in root.glob("*.parquet"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count
