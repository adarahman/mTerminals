from datetime import date

from market.expiry.instrument_expiries import (
    available_option_expiries,
    from_instrument_expiry,
    to_instrument_expiry,
)


def test_expiry_format_round_trip():
    assert to_instrument_expiry("27-Aug-2026") == "27AUG2026"
    assert from_instrument_expiry("27AUG2026") == "27-Aug-2026"


def test_available_expiries_filters_symbol_type_exchange_and_past_dates():
    records = [
        {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "20AUG2026"},
        {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "27AUG2026"},
        {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "FUTIDX", "expiry": "28AUG2026"},
        {"exch_seg": "BFO", "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "29AUG2026"},
        {"exch_seg": "NFO", "name": "BANKNIFTY", "instrumenttype": "OPTIDX", "expiry": "30AUG2026"},
    ]

    result = available_option_expiries(
        records,
        " nifty ",
        exchange="NFO",
        canonicalize=lambda value: value.strip().upper(),
        today=date(2026, 8, 24),
    )

    assert result == ["27-Aug-2026"]
