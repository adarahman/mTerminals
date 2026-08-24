from datetime import date

import pytest
import pandas as pd

from market.option_chain.requests import MarketDataRequestPlan
from market.option_chain.service import (
    ExpiryResolutionService,
    OptionChainFetchService,
)


def test_expiry_resolution_matches_same_date_in_different_format():
    service = ExpiryResolutionService()

    assert service.resolve_available(
        "27-Aug-2026", ["27AUG2026", "03SEP2026"]
    ) == "27AUG2026"


def test_expiry_resolution_selects_first_future_offer():
    service = ExpiryResolutionService()

    assert service.resolve_available(
        "missing",
        ["20-Aug-2026", "27-Aug-2026", "03-Sep-2026"],
        today=date(2026, 8, 24),
    ) == "27-Aug-2026"


def test_expiry_resolution_strict_mode_rejects_missing_request():
    with pytest.raises(RuntimeError, match="not available"):
        ExpiryResolutionService().resolve_available(
            "25-Aug-2026", ["01-Sep-2026"], strict=True
        )


def test_public_payload_preserves_requested_expiry_when_data_exists():
    payload = {
        "records": {
            "data": [{"strikePrice": 25000}],
            "expiryDates": ["01-Sep-2026"],
        }
    }

    assert ExpiryResolutionService().resolve_public_payload(
        payload, "25-Aug-2026"
    ) == "25-Aug-2026"


def _request(*, exchange="NSE", broker=False, strict=False):
    return MarketDataRequestPlan(
        symbol="SENSEX" if exchange == "BSE" else "NIFTY",
        option_expiry="27-Aug-2026",
        option_exchange=exchange,
        strict_expiry=strict,
        futures_expiry="NEAR",
        broker_enabled=broker,
    )


def _fetch_service(**overrides):
    dependencies = {
        "canonicalize_symbol": lambda symbol: symbol,
        "fetch_broker_chain": lambda symbol, expiry, exchange, strikes: pd.DataFrame(
            {"Spot": [25000.0]}
        ),
        "list_broker_expiries": lambda symbol, exchange: ["27AUG2026"],
        "fetch_public_bse_chain": lambda symbol, expiry: pd.DataFrame(
            {"Spot": [0.0]}
        ),
        "fetch_public_nse_payload": lambda symbol, expiry: {
            "records": {"data": [{}], "expiryDates": [expiry]}
        },
        "parse_public_nse_payload": lambda payload, expiry: pd.DataFrame(
            {"Spot": [24000.0]}
        ),
        "fetch_bse_quote": lambda symbol: {"Last Price": 81000.0},
        "generate_bse_expiries": lambda symbol: ["27-Aug-2026"],
    }
    dependencies.update(overrides)
    return OptionChainFetchService(**dependencies)


def test_chain_fetch_service_recovers_public_bse_spot():
    frame, spot, expiries = _fetch_service().fetch(
        _request(exchange="BSE"), strikes_each_side=10
    )

    assert spot == 81000.0
    assert frame["Spot"].iloc[0] == 81000.0
    assert expiries == ["27-Aug-2026"]


def test_chain_fetch_service_uses_canonical_broker_expiry():
    calls = []
    service = _fetch_service(
        fetch_broker_chain=lambda symbol, expiry, exchange, strikes: calls.append(
            (symbol, expiry, exchange, strikes)
        )
        or pd.DataFrame({"Spot": [25000.0]})
    )

    _, _, resolved, offered = service.fetch(
        _request(broker=True), strikes_each_side=15
    )

    assert resolved == "27AUG2026"
    assert offered == ["27AUG2026"]
    assert calls == [("NIFTY", "27AUG2026", "NFO", 15)]
