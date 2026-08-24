"""Canonical option-chain domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from core.enums import OptionType

if TYPE_CHECKING:
    from core.domain import Instrument


@dataclass
class OptionContract:
    instrument: Instrument
    strike: Decimal
    option_type: OptionType
    expiry: datetime
    ltp: Optional[Decimal]
    open_interest: int = 0
    volume: int = 0
    open_price: Optional[Decimal] = None
    high_price: Optional[Decimal] = None
    low_price: Optional[Decimal] = None
    previous_close: Optional[Decimal] = None
    net_change: Optional[Decimal] = None
    percent_change: Optional[Decimal] = None


@dataclass
class OptionChain:
    underlying: Instrument
    expiry: datetime
    contracts: list[OptionContract] = field(default_factory=list)
    spot_price: Optional[Decimal] = None
    atm_strike: Optional[Decimal] = None
