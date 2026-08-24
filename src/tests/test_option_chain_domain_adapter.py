from decimal import Decimal

import pytest

from core.enums import Exchange, InstrumentType, OptionType
from market.option_chain import option_chain_from_legacy


def test_provider_chain_maps_to_canonical_option_chain():
    chain = option_chain_from_legacy(
        {
            "underlying": "NIFTY",
            "expiry": "25AUG2026",
            "spot": 25025.5,
            "atm_strike": 25000,
            "rows": [
                {
                    "strike": 25000,
                    "type": "CE",
                    "tradingsymbol": "NIFTY25AUG2625000CE",
                    "token": 123,
                    "ltp": 120.5,
                    "oi": "4500",
                    "volume": 200,
                    "open": 100,
                    "high": 130,
                    "low": 90,
                    "close": 110,
                    "net_change": 10.5,
                    "pct_change": 9.55,
                }
            ],
        }
    )

    assert chain is not None
    assert chain.underlying.exchange is Exchange.NSE
    assert chain.spot_price == Decimal("25025.5")
    assert chain.atm_strike == Decimal("25000")
    contract = chain.contracts[0]
    assert contract.instrument.exchange is Exchange.NFO
    assert contract.instrument.instrument_type is InstrumentType.OPTION
    assert contract.instrument.option_type is OptionType.CALL
    assert contract.instrument.token == "123"
    assert contract.ltp == Decimal("120.5")
    assert contract.open_interest == 4500
    assert contract.volume == 200


def test_public_bse_chain_allows_missing_contract_identifiers():
    chain = option_chain_from_legacy(
        {
            "underlying": "SENSEX",
            "expiry": "2026-08-27",
            "rows": [{"strike": 78000, "type": "PE", "ltp": None}],
        }
    )

    assert chain is not None
    contract = chain.contracts[0]
    assert chain.underlying.exchange is Exchange.BSE
    assert contract.instrument.exchange is Exchange.BFO
    assert contract.instrument.token is None
    assert contract.ltp is None


def test_invalid_rows_are_skipped_without_discarding_valid_chain():
    chain = option_chain_from_legacy(
        {
            "underlying": "NIFTY",
            "expiry": "25-Aug-2026",
            "rows": [
                None,
                {"strike": "bad", "type": "CE"},
                {"strike": 25000, "type": "XX"},
                {"strike": 25000, "type": "PE", "ltp": 100},
            ],
        }
    )

    assert chain is not None
    assert len(chain.contracts) == 1


def test_missing_identity_or_invalid_expiry_fails_explicitly():
    with pytest.raises(ValueError):
        option_chain_from_legacy({"expiry": "25AUG2026", "rows": []})
    with pytest.raises(ValueError):
        option_chain_from_legacy(
            {"underlying": "NIFTY", "expiry": "not-a-date", "rows": []}
        )
