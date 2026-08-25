"""
mTerminals Core Errors

Business exceptions only.

Adapters translate external failures
into these domain exceptions.
"""

__all__ = [
    "MTerminalsError",
    "BrokerError",
    "AuthenticationError",
    "BrokerConnectionError",
    "OrderRejectedError",
    "MarketDataError",
    "MarketClosedError",
    "DataUnavailableError",
    "QuoteUnavailableError",
    "PricingError",
    "InstrumentError",
    "InstrumentNotFoundError",
    "InvalidInstrumentError",
    "OrderError",
    "InvalidOrderError",
    "OrderNotFoundError",
    "RiskError",
    "RiskLimitExceededError",
    "PositionLimitExceededError",
    "StrategyError",
    "DecisionError",
    "LowConfidenceDecisionError",
    "UpstoxError",
    "KiteError",
]


# ============================================================
# BASE
# ============================================================

class MTerminalsError(Exception):
    """
    Base exception for all mTerminals errors.
    """
    pass


# ============================================================
# BROKER
# ============================================================

class BrokerError(MTerminalsError):
    """
    Generic broker failure.
    """
    pass


class AuthenticationError(BrokerError):
    """
    Broker login/session/credential failure.
    """
    pass


class BrokerConnectionError(BrokerError):
    """
    Broker API/network connection failure.
    """
    pass


class OrderRejectedError(BrokerError):
    """
    Broker rejected an order.
    """
    pass


# ============================================================
# MARKET DATA
# ============================================================

class MarketDataError(MTerminalsError):
    """
    Market data related failure.
    """
    pass


class MarketClosedError(MarketDataError):
    """
    Market session is closed.
    """
    pass


class DataUnavailableError(MarketDataError):
    """
    Required market/reference data unavailable.
    """
    pass


class QuoteUnavailableError(MarketDataError):
    """
    Quote not available.
    """
    pass


# ============================================================
# PRICING
# ============================================================

class PricingError(MTerminalsError):
    """
    Option pricing/Greeks calculation failure.
    """
    pass


# ============================================================
# INSTRUMENT
# ============================================================

class InstrumentError(MTerminalsError):
    """
    Instrument related failure.
    """
    pass


class InstrumentNotFoundError(InstrumentError):
    pass


class InvalidInstrumentError(InstrumentError):
    pass


# ============================================================
# ORDER
# ============================================================

class OrderError(MTerminalsError):
    """
    Order validation failure.
    """
    pass


class InvalidOrderError(OrderError):
    pass


class OrderNotFoundError(OrderError):
    pass


# ============================================================
# RISK
# ============================================================

class RiskError(MTerminalsError):
    pass


class RiskLimitExceededError(RiskError):
    pass


class PositionLimitExceededError(RiskError):
    pass


# ============================================================
# STRATEGY / DECISION
# ============================================================

class StrategyError(MTerminalsError):
    pass


class DecisionError(MTerminalsError):
    pass


class LowConfidenceDecisionError(DecisionError):
    pass


# ============================================================
# BROKER ADAPTER (concrete transport errors)
# ============================================================

class UpstoxError(BrokerError):
    """
    Upstox adapter transport/API failure.

    Replaces the adapter-local ``RuntimeError`` subclass that used to live in
    brokers.upstox.client; adapters now raise domain exceptions from here.
    """
    pass


class KiteError(BrokerError):
    """
    Kite (Zerodha) adapter transport/API failure.

    Replaces the adapter-local ``RuntimeError`` subclass that used to live in
    brokers.kite.client; adapters now raise domain exceptions from here.
    """
    pass
