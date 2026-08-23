"""
mTerminals Core Domain

Pure business objects.
No broker imports.
No API imports.
No database imports.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


# ============================================================
# ENUMS
# ============================================================

class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"


class InstrumentType(str, Enum):
    INDEX = "INDEX"
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    OPTION = "OPTION"


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SENT = "SENT"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class DecisionStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# ============================================================
# MARKET DOMAIN
# ============================================================

@dataclass(frozen=True)
class Instrument:
    """
    NSE tradable instrument.
    """

    symbol: str
    exchange: Exchange
    instrument_type: InstrumentType

    token: Optional[str] = None

    expiry: Optional[datetime] = None
    strike: Optional[Decimal] = None
    option_type: Optional[OptionType] = None


@dataclass
class Quote:
    """
    Live market tick.
    """

    instrument: Instrument

    timestamp: datetime

    last_price: Decimal

    bid_price: Optional[Decimal] = None
    ask_price: Optional[Decimal] = None

    volume: int = 0

    open_interest: int = 0


@dataclass
class Candle:
    """
    OHLC market data.
    """

    instrument: Instrument

    timestamp: datetime

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    volume: int = 0


# ============================================================
# OPTION DOMAIN
# ============================================================

@dataclass
class OptionContract:

    instrument: Instrument

    strike: Decimal

    option_type: OptionType

    expiry: datetime

    ltp: Decimal

    open_interest: int = 0


@dataclass
class OptionChain:

    underlying: Instrument

    expiry: datetime

    contracts: list[OptionContract] = field(
        default_factory=list
    )


# ============================================================
# TRADING DOMAIN
# ============================================================

@dataclass
class Order:

    instrument: Instrument

    side: OrderSide

    quantity: int

    order_type: OrderType

    price: Optional[Decimal] = None

    status: OrderStatus = OrderStatus.CREATED


@dataclass
class Position:

    instrument: Instrument

    quantity: int

    average_price: Decimal

    unrealized_pnl: Decimal = Decimal("0")


# ============================================================
# DECISION DOMAIN
# ============================================================

@dataclass
class Signal:

    instrument: Instrument

    signal_type: SignalType

    confidence: float

    reason: str


@dataclass
class TradingDecision:

    signal: Signal

    status: DecisionStatus

    score: float

    explanation: str


# ============================================================
# RISK DOMAIN
# ============================================================

@dataclass
class RiskDecision:

    allowed: bool

    reason: str

    max_quantity: int = 0