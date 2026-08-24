"""Expiry formatting and filtering for instrument-master records."""

from collections.abc import Callable, Iterable
from datetime import date, datetime


def to_instrument_expiry(dashboard_expiry: str) -> str:
    return datetime.strptime(dashboard_expiry, "%d-%b-%Y").strftime("%d%b%Y").upper()


def from_instrument_expiry(instrument_expiry: str) -> str:
    return datetime.strptime(instrument_expiry, "%d%b%Y").strftime("%d-%b-%Y")


def available_option_expiries(
    records: Iterable[dict],
    underlying: str,
    *,
    exchange: str,
    canonicalize: Callable[[str], str],
    today: date | None = None,
) -> list[str]:
    """Return sorted, non-expired option expiries in dashboard format."""
    canonical_underlying = canonicalize(underlying)
    instrument_expiries = sorted(
        {
            row["expiry"]
            for row in records
            if row.get("exch_seg") == exchange
            and row.get("name") == canonical_underlying
            and row.get("instrumenttype") in ("OPTIDX", "OPTSTK")
            and row.get("expiry")
        },
        key=lambda value: datetime.strptime(value, "%d%b%Y"),
    )
    cutoff = today or date.today()
    return [
        from_instrument_expiry(expiry)
        for expiry in instrument_expiries
        if datetime.strptime(expiry, "%d%b%Y").date() >= cutoff
    ]
