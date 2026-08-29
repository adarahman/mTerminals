from datetime import datetime

from server.feeds.live_updates import matches_displayed_expiry


def _parse(value):
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def test_matches_equivalent_broker_expiry_formats():
    assert matches_displayed_expiry("31JUL2026", "2026-07-31", _parse)
    assert matches_displayed_expiry("31-Jul-2026", "31JUL2026", _parse)


def test_rejects_missing_invalid_or_different_expiries():
    assert not matches_displayed_expiry(None, "31JUL2026", _parse)
    assert not matches_displayed_expiry("bad", "31JUL2026", _parse)
    assert not matches_displayed_expiry("31JUL2026", "07AUG2026", _parse)
