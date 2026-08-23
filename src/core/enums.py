"""
mTerminals Core Enums

Common business states used across
market, decision, risk and execution layers.
"""

from enum import Enum


# ============================================================
# MARKET
# ============================================================

class Exchange(str, Enum):

    NSE = "NSE"
    BSE = "BSE"


class MarketSegment(str, Enum):

    CASH = "CASH"
    FUTURE = "FUTURE"
    OPTION = "OPTION"


class InstrumentType(str, Enum):

    INDEX = "INDEX"
    STOCK = "STOCK"
    FUTURE = "FUTURE"
    OPTION = "OPTION"


class OptionType(str, Enum):

    CALL = "CE"
    PUT = "PE"


# ============================================================
# ORDER
# ============================================================

class OrderSide(str, Enum):

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "STOP_LOSS"
    SL_MARKET = "STOP_LOSS_MARKET"


class ProductType(str, Enum):

    INTRADAY = "INTRADAY"
    DELIVERY = "DELIVERY"
    CARRY_FORWARD = "CARRY_FORWARD"


class OrderStatus(str, Enum):

    CREATED = "CREATED"
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"

    PARTIAL = "PARTIAL"
    FILLED = "FILLED"

    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# ============================================================
# POSITION
# ============================================================

class PositionSide(str, Enum):

    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(str, Enum):

    OPEN = "OPEN"
    CLOSED = "CLOSED"


# ============================================================
# STRATEGY / SIGNAL
# ============================================================

class SignalType(str, Enum):

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalStrength(str, Enum):

    WEAK = "WEAK"
    NORMAL = "NORMAL"
    STRONG = "STRONG"


class StrategyType(str, Enum):

    MANUAL = "MANUAL"
    RULE_BASED = "RULE_BASED"
    ML = "ML"


# ============================================================
# DECISION
# ============================================================

class DecisionStatus(str, Enum):

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WAIT = "WAIT"


class DecisionReason(str, Enum):

    SIGNAL = "SIGNAL"
    RISK = "RISK"
    MARKET_CLOSED = "MARKET_CLOSED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


# ============================================================
# RISK
# ============================================================

class RiskLevel(str, Enum):

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskAction(str, Enum):

    ALLOW = "ALLOW"
    REDUCE = "REDUCE"
    BLOCK = "BLOCK"


# ============================================================
# MARKET STATE
# ============================================================

class MarketRegime(str, Enum):

    TRENDING = "TRENDING"
    SIDEWAYS = "SIDEWAYS"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


class TradingSession(str, Enum):

    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    CLOSED = "CLOSED"