"""
strike_intervals.py
--------------------
Canonical strike-spacing table for major NSE/BSE indices, shared across
all broker adapters (SmartAPI, Breeze, Shoonya, Kotak).

RECONCILED: this table used to be independently copied into
brokers/smartapi/client.py, brokers/breeze/market_data.py,
brokers/shoonya/market_data.py, and brokers/kotak/_md/constants.py, each
with a comment explaining the duplication was intentional — importing
brokers.smartapi.client directly would pull in the smartapi-python SDK
at module load time, which a Breeze/Shoonya/Kotak-only deployment
shouldn't need just to read a constants dict. This module has no
dependencies beyond stdlib, so every broker adapter can import it
directly without that problem, the same way lot_sizes.py was pulled out
as the shared source of truth for LOT_SIZES.

Values reconciled across the four prior copies: three were identical;
smartapi/client.py's copy additionally had SENSEX50, now included here.

These are indices only; individual stock F&O strike spacing varies too
much for a fixed table and is derived per-underlying elsewhere (see
smartapi/client.py's _get_strike_interval() for the ScripMaster-derived
fallback stocks use).
"""

from __future__ import annotations

STRIKE_INTERVALS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "MIDCPNIFTY": 25,
    "SENSEX": 100,
    "BANKEX": 100,
    "SENSEX50": 50,
}
