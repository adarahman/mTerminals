"""Focused builders for canonical dashboard payload sections."""

from .signals import build_signals
from .common import (
    compact_number,
    formatted_number,
    integer,
    nullable_rounded_number,
    rounded_number,
    safe_string,
)
from .market_rows import build_bid_ask_map, build_capital_map, build_chain_rows
from .strategies import build_strategies
from .risk import build_risk
from .decision import build_decision
from .greeks import build_greeks_rows
from .oi_velocity import build_oi_velocity
from .export_sections import (
    apply_expiry_context,
    build_extra_chains,
    build_vol_oi_ratios,
)

__all__ = [
    "build_signals",
    "compact_number",
    "formatted_number",
    "integer",
    "nullable_rounded_number",
    "rounded_number",
    "safe_string",
    "build_bid_ask_map",
    "build_capital_map",
    "build_chain_rows",
    "build_strategies",
    "build_risk",
    "build_decision",
    "build_greeks_rows",
    "build_oi_velocity",
    "apply_expiry_context",
    "build_extra_chains",
    "build_vol_oi_ratios",
]
