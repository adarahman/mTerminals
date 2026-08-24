from datetime import datetime, timezone
from decimal import Decimal

from core.enums import Exchange, InstrumentType
from market.quotes import quote_from_legacy


def test_legacy_spot_quote_maps_to_core_quote():
    observed_at = datetime(2026, 8, 24, 4, 15, tzinfo=timezone.utc)
    quote = quote_from_legacy(
        "NIFTY",
        {
            "ltp": 25123.45,
            "open": "25000",
            "high": 25200,
            "low": 24950,
            "close": 25050,
            "volume": "1200",
            "oi": 450,
        },
        token="99926000",
        timestamp=observed_at,
    )

    assert quote is not None
    assert quote.instrument.symbol == "NIFTY"
    assert quote.instrument.exchange is Exchange.NSE
    assert quote.instrument.instrument_type is InstrumentType.INDEX
    assert quote.instrument.token == "99926000"
    assert quote.timestamp == observed_at
    assert quote.last_price == Decimal("25123.45")
    assert quote.previous_close == Decimal("25050")
    assert quote.volume == 1200
    assert quote.open_interest == 450


def test_bse_index_exchange_is_inferred_without_broker_dependency():
    quote = quote_from_legacy("SENSEX", {"ltp": 78000})

    assert quote is not None
    assert quote.instrument.exchange is Exchange.BSE


def test_missing_or_invalid_ltp_remains_unavailable():
    assert quote_from_legacy("NIFTY", None) is None
    assert quote_from_legacy("NIFTY", {"ltp": None}) is None
    assert quote_from_legacy("NIFTY", {"ltp": "not-a-number"}) is None
