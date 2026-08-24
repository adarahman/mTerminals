from decimal import Decimal

import pytest

from core.enums import Exchange, InstrumentType, OptionType
from market.instruments import instrument_from_execution_resolution


def test_option_resolution_maps_to_canonical_instrument():
    instrument = instrument_from_execution_resolution(
        "nifty",
        "CE",
        "25AUG2026",
        25000,
        ("NFO", "NIFTY25AUG2625000CE", 12345),
    )

    assert instrument.symbol == "NIFTY"
    assert instrument.exchange is Exchange.NFO
    assert instrument.instrument_type is InstrumentType.OPTION
    assert instrument.trading_symbol == "NIFTY25AUG2625000CE"
    assert instrument.token == "12345"
    assert instrument.expiry.strftime("%Y-%m-%d") == "2026-08-25"
    assert instrument.strike == Decimal("25000")
    assert instrument.option_type is OptionType.CALL


def test_non_option_resolution_does_not_invent_option_fields():
    instrument = instrument_from_execution_resolution(
        "RELIANCE", "EQ", "", None, ("NSE", "RELIANCE-EQ", "2885")
    )

    assert instrument.instrument_type is InstrumentType.EQUITY
    assert instrument.expiry is None
    assert instrument.strike is None
    assert instrument.option_type is None


@pytest.mark.parametrize(
    "resolved",
    [None, ("NFO", "ONLY_TWO"), ("UNKNOWN", "SYMBOL", "1"), ("NFO", "", "1")],
)
def test_malformed_resolution_fails_closed(resolved):
    with pytest.raises(ValueError):
        instrument_from_execution_resolution(
            "NIFTY", "PE", "25AUG2026", 25000, resolved
        )
