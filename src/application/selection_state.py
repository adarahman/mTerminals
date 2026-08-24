"""Process-wide market selection owned by the application layer."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarketSelectionState:
    symbol: str
    expiry: str | None
    data_source: str
    price_source: str = "AUTO"
    futures_expiry: str = "NEAR"

    def select_symbol(self, symbol: str, expiry: str | None) -> None:
        self.symbol = symbol
        self.expiry = expiry

    def select_data_source(self, data_source: str) -> None:
        self.data_source = data_source

    def select_price_source(self, price_source: str) -> None:
        self.price_source = price_source

    def select_futures_expiry(self, futures_expiry: str) -> None:
        self.futures_expiry = futures_expiry

    def snapshot(self) -> dict[str, str | None]:
        return {
            "symbol": self.symbol,
            "expiry": self.expiry,
            "data_source": self.data_source,
            "price_source": self.price_source,
            "futures_expiry": self.futures_expiry,
        }
