"""Provider-neutral request planning for option-chain market data."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketDataRequestPlan:
    symbol: str
    option_expiry: str
    option_exchange: str
    strict_expiry: bool
    futures_expiry: str
    broker_enabled: bool

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        option_expiry = self.option_expiry.strip()
        option_exchange = self.option_exchange.strip().upper()
        futures_expiry = self.futures_expiry.strip().upper()
        if not symbol:
            raise ValueError("symbol cannot be empty")
        if not option_expiry:
            raise ValueError("option_expiry cannot be empty")
        if option_exchange not in {"NSE", "BSE"}:
            raise ValueError("option_exchange must be NSE or BSE")
        if futures_expiry not in {"NEAR", "NEXT", "FAR"}:
            raise ValueError("futures_expiry must be NEAR, NEXT, or FAR")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "option_expiry", option_expiry)
        object.__setattr__(self, "option_exchange", option_exchange)
        object.__setattr__(self, "futures_expiry", futures_expiry)

    @property
    def broker_derivatives_exchange(self) -> str:
        return "BFO" if self.option_exchange == "BSE" else "NFO"
