from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class MarketData:
    symbol: str
    data: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class GroundingProvider(Protocol):
    def fetch_market_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> MarketData: ...

    def is_available(self) -> bool: ...


class DummyGroundingProvider:
    def __init__(self) -> None:
        self._available = True

    def fetch_market_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> MarketData:
        return MarketData(
            symbol=symbol,
            data={"start": start_date, "end": end_date, "source": "dummy"},
        )

    def is_available(self) -> bool:
        return self._available
