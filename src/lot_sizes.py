"""
lot_sizes.py
------------
Lot-size resolution for the option chain pipeline: a dict-like
`LOT_SIZES` singleton (and a `get_lot_size()` function) that resolves
through live FUT-derived lot sizes (smartapi_instruments.get_lot_size(),
sourced from the AngelOne instrument master), falling back to the
last-known-good value for that symbol, and finally to a small static
emergency-fallback table if both of those fail.

Moved from option_chain_json.py (Step 5b of the v4 migration plan).

RECONCILED (this pass): this used to be a two-tier fallback (live ->
static table) while paper_trading.py independently maintained a richer
three-tier version (live -> last-known-good cache -> its own static
table) plus its own `_LiveLotSizes` dict subclass. This module now owns
the canonical three-tier logic; paper_trading.py imports from here
instead of keeping its own copy. See paper_trading.py's docstring for
the back-compat shim it still exports.

Value conflict found and resolved: the two prior static tables disagreed
on FINNIFTY (60 here vs. 65 in paper_trading.py's copy) — confirmed 60
is correct; paper_trading.py's stale 65 is gone now that it imports from
here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = [
    "LOT_SIZES",
    "get_lot_size",
]


# Emergency fallback only — live lot sizes come from FUTSTK/FUTIDX rows in
# the AngelOne master via smartapi_instruments.get_lot_size(). These static
# numbers go stale on every NSE quarterly lot revision; never add stocks
# here hoping to cover the universe (there are 200+).
_STATIC_LOT_SIZES = {
    "NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120,
    "SENSEX": 20, "BANKEX": 30, "SENSEX50": 75, "PNB": 8000,
}

# symbol -> last-known-good lot size, populated the first time each symbol
# resolves successfully via smartapi_instruments. Avoids re-hitting the
# resolver (and its network/master-file cost) on every call, while still
# tracking whatever's currently live instead of a hardcoded snapshot that
# goes stale after NSE's quarterly lot revisions.
_lot_size_cache: dict[str, int] = {}


def get_lot_size(symbol: str, default: int | None = 65) -> int:
    """Resolves the *current* lot size for `symbol` off the live AngelOne
    instrument master, not a hardcoded table. Falls back to the
    last-known-good value for this symbol, then to `_STATIC_LOT_SIZES`,
    only if the live lookup itself fails — never silently substitutes
    some *other* symbol's lot size.

    Raises KeyError if `default` is None and every tier misses; otherwise
    returns `default` as the final fallback (kept for callers that used
    the old `.get(sym, default)` shape with a caller-supplied default).
    """
    sym = (symbol or "").upper()
    try:
        from brokers.smartapi_instruments import get_lot_size as _resolve
        lot = _resolve(sym)
        _lot_size_cache[sym] = lot
        return lot
    except Exception as e:
        if sym in _lot_size_cache:
            logger.warning(
                "Lot size lookup failed for %s (%s); using last-known value %d",
                sym, e, _lot_size_cache[sym],
            )
            return _lot_size_cache[sym]
        if sym in _STATIC_LOT_SIZES:
            logger.warning(
                "Lot size lookup failed for %s (%s); no cached value, using "
                "static fallback %d — VERIFY against current NSE circular.",
                sym, e, _STATIC_LOT_SIZES[sym],
            )
            return _STATIC_LOT_SIZES[sym]
        if default is not None:
            return default
        raise KeyError(
            f"Cannot resolve lot size for '{sym}': live lookup failed ({e}) "
            f"and no fallback registered."
        ) from e


class _LiveLotSizes(dict):
    """dict-like shim so existing `LOT_SIZES.get(sym, default)` /
    `LOT_SIZES[sym]` call sites keep working, resolving live (with
    caching) instead of through a hard-coded table. Misses are resolved
    via get_lot_size() on first access and cached on the dict itself, so
    repeated lookups don't re-hit the resolver."""

    def __missing__(self, key):
        try:
            value = get_lot_size(key, default=None)
        except Exception:
            raise KeyError(key)
        self[key] = value
        return value

    def get(self, key, default=65):
        try:
            return self[key]
        except KeyError:
            return default


LOT_SIZES = _LiveLotSizes()
