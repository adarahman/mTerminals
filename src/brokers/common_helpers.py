"""
common_helpers.py
------------------
Small pure-function helpers duplicated verbatim across broker adapters
(brokers/smartapi/client.py, brokers/kite/client.py,
brokers/shoonya/market_data.py for safe_float; brokers/breeze/market_data.py,
brokers/shoonya/market_data.py, brokers/kotak/_md/contracts.py for
_round_to_strike). Consolidated here the same way lot_sizes.py and
strike_intervals.py were pulled out as shared sources of truth.

NOTE: this is NOT the same function as market/providers/nse_bse_client.py's
own `safe_float` — that one has a different signature (no default arg) and
extra string-cleanup logic for the NSE/BSE public API's messier JSON
(commas, "-", "—" placeholders). That one is intentionally left as its own
implementation; do not consolidate it with this one without checking both
call sites' expectations first.
"""
from __future__ import annotations

from market.instruments.strike_intervals import STRIKE_INTERVALS as _STRIKE_INTERVALS


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def round_to_strike(price, underlying):
    interval = _STRIKE_INTERVALS.get(str(underlying).upper(), 50)
    return int(round(price / interval) * interval)
