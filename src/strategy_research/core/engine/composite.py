"""CompositeEngine — 跨市场组合引擎。

共享资金池，委托子引擎规则方法。
"""

from __future__ import annotations

from typing import Dict

import pandas as pd

from .base import BaseEngine
from .china_a import ChinaAEngine
from .crypto import CryptoEngine
from .forex import ForexEngine
from .global_equity import GlobalEquityEngine
from .market_hooks import calc_crypto_funding_fee, check_crypto_liquidation
from .models import Position


def _detect_market_simple(symbol: str) -> str:
    """简化版市场检测。"""
    if "." in symbol:
        suffix = symbol.split(".")[-1].upper()
        if suffix in ("SZ", "SH", "BJ"):
            return "a_share"
        if suffix == "HK":
            return "hk_equity"
        if suffix in ("NS", "BO"):
            return "india_equity"
    if "-" in symbol or "/" in symbol:
        return "crypto"
    if len(symbol) <= 5 and symbol.isalpha():
        return "us_equity"
    return "a_share"


class CompositeEngine(BaseEngine):
    """跨市场组合引擎：共享资金池 + 委托子引擎规则。"""

    def __init__(self, config: dict, codes: list[str]):
        super().__init__(config)
        self._symbol_market = {c: _detect_market_simple(c) for c in codes}
        self._rule_engines: Dict[str, BaseEngine] = self._build_rule_engines(config)
        # Crypto dedup state
        self._funding_applied: set = set()
        self._funding_daily_done: set = set()
        # Forex dedup state
        self._last_swap_dates: dict = {}

    def _build_rule_engines(self, config: dict) -> Dict[str, BaseEngine]:
        return {
            "a_share": ChinaAEngine(config),
            "us_equity": GlobalEquityEngine(config, market="us"),
            "hk_equity": GlobalEquityEngine(config, market="hk"),
            "crypto": CryptoEngine(config),
            "forex": ForexEngine(config),
        }

    def _rule_for(self, symbol: str) -> BaseEngine:
        market = self._symbol_market.get(symbol, "a_share")
        return self._rule_engines.get(market, self._rule_engines["a_share"])

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        market = self._symbol_market.get(symbol, "a_share")
        # T+1 interceptor for A-share
        if market == "a_share" and direction == 0:
            pos = self.positions.get(symbol)
            if pos is not None:
                bar_date = bar.name.date() if hasattr(bar, "name") else None
                entry_date = pos.entry_time.date() if hasattr(pos.entry_time, "date") else None
                if bar_date and entry_date and bar_date == entry_date:
                    return False
        return self._rule_for(symbol).can_execute(symbol, direction, bar)

    def round_size(self, raw_size: float, price: float) -> float:
        return self._rule_for(self._active_symbol).round_size(raw_size, price)

    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        return self._rule_for(self._active_symbol).calc_commission(size, price, direction, is_open)

    def apply_slippage(self, price: float, direction: int) -> float:
        sub = self._rule_for(self._active_symbol)
        return sub.apply_slippage(price, direction)

    def _calc_pnl(self, symbol, direction, size, entry_price, exit_price):
        return self._rule_for(symbol)._calc_pnl(symbol, direction, size, entry_price, exit_price)

    def _calc_margin(self, symbol, size, price, leverage):
        return self._rule_for(symbol)._calc_margin(symbol, size, price, leverage)

    def on_bar(self, symbol: str, bar: pd.Series, timestamp: pd.Timestamp) -> None:
        market = self._symbol_market.get(symbol)
        if market == "crypto":
            crypto_sub = self._rule_engines["crypto"]
            fee = calc_crypto_funding_fee(
                symbol, bar, timestamp, self.positions,
                crypto_sub.funding_rate, self._funding_applied, self._funding_daily_done,
            )
            self.capital -= fee
            if check_crypto_liquidation(symbol, bar, self.positions):
                pos = self.positions.get(symbol)
                if pos is not None:
                    mark_price = float(bar.get("close", pos.entry_price))
                    liq_price = crypto_sub.apply_slippage(mark_price, -pos.direction)
                    self._close_position(symbol, liq_price, timestamp, "liquidation")
        elif market == "forex":
            forex_sub = self._rule_engines["forex"]
            if forex_sub.swap_enabled:
                current_date = timestamp.date()
                last_swap = self._last_swap_dates.get(symbol)
                if last_swap != current_date:
                    pos = self.positions.get(symbol)
                    if pos is not None:
                        swap_rate = forex_sub.swap_long if pos.direction == 1 else forex_sub.swap_short
                        notional = pos.size * float(bar.get("close", pos.entry_price))
                        is_wed = timestamp.weekday() == 2
                        multiplier = 3 if is_wed else 1
                        swap = notional * swap_rate * multiplier * 0.0001
                        self.capital += swap
                        self._last_swap_dates[symbol] = current_date


__all__ = ["CompositeEngine"]