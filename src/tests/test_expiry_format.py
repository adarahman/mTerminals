import pytest

from brokers.expiry_format import to_compact_expiry, to_dash_expiry


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("31JUL2026", "31-Jul-2026"),
        ("31-Jul-2026", "31-Jul-2026"),
        ("2026-07-31", "31-Jul-2026"),
        (None, None),
    ],
)
def test_to_dash_expiry(value, expected):
    assert to_dash_expiry(value) == expected


def test_to_dash_expiry_rejects_unknown_formats():
    with pytest.raises(ValueError, match="Unsupported expiry format"):
        to_dash_expiry("31/07/2026")


def test_to_compact_expiry():
    assert to_compact_expiry("31-Jul-2026") == "31JUL2026"
    assert to_compact_expiry(None) is None
