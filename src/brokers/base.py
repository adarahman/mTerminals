"""Broker-neutral contracts implemented by external provider adapters."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class MarketDataProvider(Protocol):
    def list_expiries(self, underlying: str, exchange: str = "NFO") -> list: ...

    def get_atm_chain(
        self,
        underlying: str,
        expiry_ddmmmyyyy: str,
        strikes_around_atm: int = 10,
        exchange: str = "NFO",
    ) -> Optional[dict]: ...

    def find_option_token(
        self,
        underlying: str,
        expiry_ddmmmyyyy: str,
        strike,
        opt_type: str,
        exchange: str = "NFO",
    ) -> Optional[dict]: ...

    def get_batch_quotes(
        self, exchange: str, symbol_token_pairs: list, mode: str = "FULL"
    ) -> dict: ...

    def get_batch_quotes_by_token(
        self, exchange: str, symbol_token_pairs: list, mode: str = "FULL"
    ) -> dict: ...

    def get_spot_quote(self, underlying: str) -> Optional[dict]: ...

    def get_futures_quote(
        self, underlying: str, which: str = "NEAR"
    ) -> Optional[dict]: ...

    def get_fno_underlyings(self, force_refresh: bool = False) -> dict: ...

    def index_tokens(self) -> dict: ...


@runtime_checkable
class ExecutionBroker(Protocol):
    def place_order(
        self,
        tradingsymbol,
        symboltoken,
        exchange,
        transaction_type,
        quantity,
        order_type="MARKET",
        product_type="INTRADAY",
        price=0.0,
        variety="NORMAL",
        order_tag=None,
    ): ...

    def get_order_book(self) -> list: ...

    def get_positions(self) -> list: ...

    def get_funds(self) -> dict: ...


EXECUTION_REQUIRED_METHODS = (
    "place_order",
    "get_order_book",
    "get_positions",
    "get_funds",
)


def missing_execution_methods(adapter) -> list[str]:
    """Return missing common operations without importing any broker SDK."""
    return [
        method
        for method in EXECUTION_REQUIRED_METHODS
        if not callable(getattr(adapter, method, None))
    ]


# Compatibility name retained for callers that used brokers.market_data.MarketData.
MarketData = MarketDataProvider

__all__ = [
    "MarketData",
    "MarketDataProvider",
    "ExecutionBroker",
    "EXECUTION_REQUIRED_METHODS",
    "missing_execution_methods",
]
