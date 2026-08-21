"""Shared expiry comparison for normalized broker tick streams."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date


def matches_displayed_expiry(
    streamed_expiry: str | None,
    payload_expiry: str | None,
    parse_expiry: Callable[[str], date | None],
) -> bool:
    """True only when a streamed tick belongs to the displayed expiry."""
    if not streamed_expiry or not payload_expiry:
        return False
    streamed_date = parse_expiry(streamed_expiry)
    payload_date = parse_expiry(payload_expiry)
    return streamed_date is not None and streamed_date == payload_date
