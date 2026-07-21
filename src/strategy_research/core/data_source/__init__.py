"""数据源模块。

提供统一的数据获取接口，支持多个数据源和 fallback 机制。
"""

from .registry import (
    resolve_loader,
    resolve_loader_with_fallback,
    get_loader_or_fallback,
    list_loaders,
    NoAvailableSourceError,
    FALLBACK_CHAINS,
)
from .base import DataLoader, validate_date_range, validate_ohlc, normalize_ohlcv
from .utils import (
    load_tokens,
    get_token,
    detect_market,
    normalize_code,
    is_a_share,
    is_etf,
    is_index,
    is_hk,
    is_us,
    is_forex,
    is_crypto,
    is_fred_series,
)

__all__ = [
    # 注册表
    "resolve_loader",
    "resolve_loader_with_fallback",
    "get_loader_or_fallback",
    "list_loaders",
    "NoAvailableSourceError",
    "FALLBACK_CHAINS",
    # 基础
    "DataLoader",
    "validate_date_range",
    "validate_ohlc",
    "normalize_ohlcv",
    # 工具
    "load_tokens",
    "get_token",
    "detect_market",
    "normalize_code",
    "is_a_share",
    "is_etf",
    "is_index",
    "is_hk",
    "is_us",
    "is_forex",
    "is_crypto",
    "is_fred_series",
]
