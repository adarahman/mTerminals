"""
Backward-compatible exception imports.

New code should import directly from core.errors.
This module remains temporarily so older imports do not break.
"""

from core.errors import (
    MTerminalsError,
    BrokerError,
    AuthenticationError,
    BrokerConnectionError,
    OrderRejectedError,
    MarketDataError,
    MarketClosedError,
    DataUnavailableError,
    QuoteUnavailableError,
    PricingError,
    InstrumentError,
    InstrumentNotFoundError,
    InvalidInstrumentError,
    OrderError,
    InvalidOrderError,
    OrderNotFoundError,
    RiskError,
    RiskLimitExceededError,
    PositionLimitExceededError,
    StrategyError,
    DecisionError,
    LowConfidenceDecisionError,
)

# Historical name retained temporarily.
BackendError = MTerminalsError

__all__ = [
    "MTerminalsError",
    "BackendError",
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
]
