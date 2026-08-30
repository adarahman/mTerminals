"""Shared Kotak Neo market-data constants.

Kept in their own module so scrip_master / contracts / quotes can share them
without creating an import cycle through the package ``__init__``.

_STRIKE_INTERVALS below is re-exported from market.instruments.strike_intervals,
the canonical table shared with the SmartAPI/Breeze/Shoonya adapters — see
that module for why it's standalone rather than importing
brokers.smartapi.client directly (that would pull in the smartapi-python SDK
just to read a constants dict).
"""

from market.instruments.strike_intervals import STRIKE_INTERVALS as _STRIKE_INTERVALS

# Index names Kotak's quotes API expects for spot/index tokens (the docs
# list these literal display names — NSE indices do NOT take a numeric
# instrument token). Keyed by the codebase's underlying symbol.
_INDEX_NAMES = {
    "NIFTY": "Nifty 50",
    "BANKNIFTY": "Nifty Bank",
    "FINNIFTY": "Nifty Fin Service",
    "MIDCPNIFTY": "Nifty Midcap Select",
    "INDIA VIX": "India VIX",
}

_BSE_INDEX_NAMES = {
    "SENSEX": "SENSEX",
    "BANKEX": "BANKEX",
}

# Indices with exchange-segment mapping; everything else is NFO stock
# derivatives.
_INDEX_EXCHANGE = {
    "NSE": "nse_cm",
    "BSE": "bse_cm",
    "NFO": "nse_fo",
    "BFO": "bse_fo",
}
