"""Shared broker expiry-format conversions."""
from __future__ import annotations

from datetime import datetime


def to_dash_expiry(expiry):
    """Convert supported broker expiry formats to ``DD-Mon-YYYY``."""
    if not expiry:
        return expiry
    for date_format in ("%d-%b-%Y", "%d%b%Y", "%Y-%m-%d"):
        try:
            # These values represent exchange dates, not instants in time.
            return datetime.strptime(  # noqa: DTZ007
                str(expiry), date_format
            ).strftime("%d-%b-%Y")
        except ValueError:
            continue
    raise ValueError(f"Unsupported expiry format: {expiry!r}")


def to_compact_expiry(expiry):
    """Convert ``DD-Mon-YYYY`` to the broker boundary's ``DDMMMYYYY``."""
    if not expiry:
        return expiry
    return (
        datetime.strptime(  # noqa: DTZ007
            str(expiry), "%d-%b-%Y"
        ).strftime("%d%b%Y").upper()
    )
