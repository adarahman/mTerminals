"""
Shared market pipeline utilities.
"""
from market.option_chain.oi_change import PreviousCloseOiTracker

def safe_float(value, default=0.0):
    """
    Convert API values safely to float.
    Handles None, empty strings, malformed broker responses.
    """
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
    
def lot_size(underlying: str) -> int:
    """
    Return lot size for underlying.
    """
    lots = {
        "NIFTY": 75,
        "BANKNIFTY": 35,
        "FINNIFTY": 65,
        "MIDCPNIFTY": 140,
    }
    return lots.get(str(underlying).upper(), 1)


from brokers.symbol_names import (
    _COMMON_UNDERLYING_ALIASES,
    canonicalize_underlying,
)


def _canon_underlying(underlying: str) -> str:
    raw = str(underlying or "").strip().upper()
    if not raw:
        return ""

    # Shared company-name -> exchange-ticker resolver.
    resolved = canonicalize_underlying(
        raw,
        _COMMON_UNDERLYING_ALIASES,
    )
    if resolved:
        return resolved

    # Index aliases.
    compact = raw.replace(" ", "").replace("-", "")
    aliases = {
        "BANKNIFTY": "BANKNIFTY",
        "NIFTYBANK": "BANKNIFTY",
        "NIFTY": "NIFTY",
        "FINNIFTY": "FINNIFTY",
        "MIDCPNIFTY": "MIDCPNIFTY",
    }
    return aliases.get(compact, compact)


# Public alias for new code.
canon_underlying = _canon_underlying

    