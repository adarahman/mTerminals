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


def _canon_underlying(symbol: str) -> str:
    """
    Backward-compatible symbol normalization.
    """
    return (
        str(symbol)
        .upper()
        .replace(" ", "")
        .replace("-", "")
    )

def _canon_underlying(underlying: str) -> str:
    value = str(underlying or "").upper().replace(" ", "")

    aliases = {
        "BANKNIFTY": "BANKNIFTY",
        "NIFTYBANK": "BANKNIFTY",
        "NIFTY": "NIFTY",
        "FINNIFTY": "FINNIFTY",
        "MIDCPNIFTY": "MIDCPNIFTY",
    }

    return aliases.get(value, value)
# public alias for new code
canon_underlying = _canon_underlying

    