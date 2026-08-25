"""Shared Kotak Neo market-data constants.

Kept in their own module so scrip_master / contracts / quotes can share them
without creating an import cycle through the package ``__init__``.

Same physical strike spacing SmartAPI's STRIKE_INTERVALS uses — kept as an
independent copy rather than importing brokers.smartapi.client (that module
imports the SmartApi SDK at module top level, which would make a Kotak-only
deployment depend on smartapi-python being installed just to read a
constants dict). Same category of duplication as the two LOT_SIZES dicts
already tracked as a dedup TODO elsewhere in this codebase.
"""
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

# Strike spacing per underlying (rupee convention, post-normalization).
_STRIKE_INTERVALS = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "MIDCPNIFTY": 25, "SENSEX": 100, "BANKEX": 100,
}
