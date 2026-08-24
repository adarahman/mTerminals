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
from typing import Any, Optional
from .enums import (
    Exchange,
    InstrumentType,
    OptionType,
    OrderSide,
    OrderType,
    OrderStatus,
    SignalType,
    DecisionStatus,
)

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
    trading_symbol: Optional[str] = None

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

    open_price: Optional[Decimal] = None
    high_price: Optional[Decimal] = None
    low_price: Optional[Decimal] = None
    previous_close: Optional[Decimal] = None

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


# Option-chain models live in their bounded context. These re-exports keep
# historical ``core.domain`` imports working during the migration.
from .market.option_chain.models import OptionChain, OptionContract


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
    order_id: Optional[str] = None
    created_at: Optional[datetime] = None
    filled_price: Optional[Decimal] = None
    filled_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


@dataclass
class Position:

    instrument: Instrument

    quantity: int

    average_price: Decimal

    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    last_price: Optional[Decimal] = None


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


# ============================================================
# DECISION OUTPUT CONTRACT
# ============================================================

_SIGNAL_SEVERITY_ORDER = {"warn": 0, "ok": 1, "info": 2}


@dataclass
class ActiveSignal:
    text: str
    severity: str = "info"
    priority: int = 99
    signal_id: str = ""


@dataclass
class DecisionResult:
    """Canonical point-in-time output from the decision engine."""

    decision_timestamp: str = ""
    state_version: str = ""
    stale: bool = False
    degraded: bool = False
    evidence_coverage: int = 0
    missing_inputs: list[str] = field(default_factory=list)
    contributors: list[dict[str, Any]] = field(default_factory=list)
    bias: str = "NEUTRAL"
    bias_strength: str = "WEAK"
    confidence: int = 0
    conflict_flag: bool = False
    action: str = ""
    action_type: str = "WAIT"
    suggested_strike: Optional[int] = None
    suggested_strategy: str = ""
    auto_strategy: dict[str, Any] = field(default_factory=dict)
    execute_recommended: bool = True
    strategy_caution: str = ""
    active_signals: list[ActiveSignal] = field(default_factory=list)
    verdicts: dict[str, Any] = field(default_factory=dict)
    oi_annotations: dict[str, Any] = field(default_factory=dict)
    trade_grade: str = ""
    risk_warning: str = ""
    important_levels: dict[str, Any] = field(default_factory=dict)
    _debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        ordered = sorted(
            self.active_signals,
            key=lambda signal: (
                _SIGNAL_SEVERITY_ORDER.get(signal.severity, 9),
                signal.priority,
            ),
        )
        signals: list[ActiveSignal] = []
        seen: set[str] = set()
        for signal in ordered:
            identity = signal.signal_id or signal.text
            if identity in seen:
                continue
            seen.add(identity)
            signals.append(signal)
        return {
            "decisionTimestamp": self.decision_timestamp,
            "stateVersion": self.state_version,
            "stale": self.stale,
            "degraded": self.degraded,
            "evidenceCoverage": self.evidence_coverage,
            "missingInputs": self.missing_inputs,
            "contributors": self.contributors,
            "bias": self.bias,
            "biasStrength": self.bias_strength,
            "confidence": self.confidence,
            "conflictFlag": self.conflict_flag,
            "action": self.action,
            "actionType": self.action_type,
            "suggestedStrike": self.suggested_strike,
            "suggestedStrategy": self.suggested_strategy,
            "executeRecommended": self.execute_recommended,
            "strategyCaution": self.strategy_caution,
            "activeSignals": [
                {
                    "id": signal.signal_id or signal.text,
                    "text": signal.text,
                    "severity": signal.severity,
                    "priority": signal.priority,
                    "observedAt": self.decision_timestamp,
                }
                for signal in signals
            ],
            "verdicts": self.verdicts,
            "oiAnnotations": self.oi_annotations,
            "tradeGrade": self.trade_grade,
            "riskWarning": self.risk_warning,
            "importantLevels": self.important_levels,
            "autoStrategy": self.auto_strategy,
            "_debug": self._debug,
        }


Decision = DecisionResult
