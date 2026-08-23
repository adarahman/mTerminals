"""
mTerminals Ports

Interfaces used by application layer.

Broker implementations,
database,
websocket,
API adapters
must implement these.
"""

from typing import Protocol, Iterable

from .domain import (
    Instrument,
    Quote,
    Candle,
    Order,
    Position,
    OptionChain,
)


# ============================================================
# MARKET DATA
# ============================================================

class MarketDataProvider(Protocol):

    def get_quote(
        self,
        instrument: Instrument
    ) -> Quote:
        ...


class HistoricalDataProvider(Protocol):

    def candles(
        self,
        instrument: Instrument,
        timeframe: str
    ) -> Iterable[Candle]:
        ...


class OptionChainProvider(Protocol):

    def get_option_chain(
        self,
        underlying: Instrument,
        expiry
    ) -> OptionChain:
        ...


# ============================================================
# ORDER EXECUTION
# ============================================================

class OrderExecutor(Protocol):

    def place_order(
        self,
        order: Order
    ) -> Order:
        ...


class PositionProvider(Protocol):

    def positions(self) -> list[Position]:
        ...


# ============================================================
# STORAGE
# ============================================================

class InstrumentRepository(Protocol):

    def find(
        self,
        symbol: str
    ) -> Instrument:
        ...


class QuoteRepository(Protocol):

    def save(
        self,
        quote: Quote
    ):
        ...


# ============================================================
# NOTIFICATION
# ============================================================

class NotificationService(Protocol):

    def send(
        self,
        message: str
    ):
        ...