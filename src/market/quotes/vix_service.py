"""India VIX resolution from broker quotes with a public-market fallback."""

from collections.abc import Callable
from typing import Any


def resolve_vix(
    broker_quote: dict | None,
    *,
    public_loader: Callable[[], tuple[Any, Any, Any]],
    safe_number: Callable[[Any], float | None],
    warn: Callable[[str, str], None],
) -> tuple[float | None, float]:
    """Return broker VIX when usable, otherwise try the public NSE source."""
    ltp = safe_number(broker_quote.get("ltp")) if broker_quote else None
    if ltp:
        close = safe_number(broker_quote.get("close"))
        change_percent = round((ltp - close) / close * 100.0, 2) if close else 0.0
        return ltp, change_percent

    try:
        public_vix, public_change, _ = public_loader()
        public_vix = safe_number(public_vix)
        if public_vix:
            warn(
                "vix:public-fallback",
                "VIX missing from broker quote; using public NSE VIX fallback",
            )
            return public_vix, safe_number(public_change) or 0.0
    except Exception as error:
        warn("vix:public-fallback", f"Public NSE VIX fallback failed: {error}")

    warn("vix:missing", "VIX unavailable from broker and public NSE fallback")
    return None, 0.0
