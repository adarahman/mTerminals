"""
mTerminals Core Domain

Pure business objects.
No broker imports.
No API imports.
No database imports.
"""

from enum import Enum

# ============================================================
# MARKET
# ============================================================

class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"
    BFO = "BFO"


class InstrumentType(str, Enum):

    INDEX = "INDEX"
    STOCK = "STOCK"
    # Compatibility with the original core.domain vocabulary.  Keep both
    # names during migration because broker instrument masters commonly use
    # either STOCK or EQUITY.
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    OPTION = "OPTION"


class OptionType(str, Enum):

    CALL = "CE"
    PUT = "PE"
    # Historical names retained as aliases while callers move to CALL/PUT.
    CE = "CE"
    PE = "PE"


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


class OrderStatus(str, Enum):

    CREATED = "CREATED"
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    SENT = "SUBMITTED"

    PARTIAL = "PARTIAL"
    FILLED = "FILLED"

    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
