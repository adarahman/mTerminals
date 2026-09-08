from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from market.providers import nse_bse_client as market_api


def test_public_nse_futures_selects_requested_contract_without_broker(monkeypatch):
    expiries = [date.today() + timedelta(days=n) for n in (5, 35, 65)]
    rows = pd.DataFrame([
        {"Contract": f"NIFTY-{i}", "Underlying": "NIFTY", "Expiry": expiry.strftime("%d-%b-%Y"), "LTP": 24000 + i * 100}
        for i, expiry in enumerate(expiries)
    ])
    monkeypatch.setattr(market_api, "fetch_nifty_futures", lambda index: rows.copy())

    assert market_api.fetch_public_futures("NIFTY", "NEAR").iloc[0]["LTP"] == 24000
    assert market_api.fetch_public_futures("NIFTY", "NEXT").iloc[0]["LTP"] == 24100
    assert market_api.fetch_public_futures("NIFTY", "FAR").iloc[0]["LTP"] == 24200


def test_public_stock_futures_filters_underlying(monkeypatch):
    expiry = (date.today() + timedelta(days=10)).strftime("%d-%b-%Y")
    rows = pd.DataFrame([
        {"Contract": "OTHER", "Underlying": "OTHER", "Expiry": expiry, "LTP": 10},
        {"Contract": "RELIANCE", "Underlying": "RELIANCE", "Expiry": expiry, "LTP": 3000},
    ])
    monkeypatch.setattr(market_api, "fetch_nifty_futures", lambda index: rows.copy())

    selected = market_api.fetch_public_futures("RELIANCE", "NEAR")
    assert selected.iloc[0]["Underlying"] == "RELIANCE"


def test_futures_reference_never_replaces_option_spot():
    source = (
        Path(__file__).resolve().parents[1]
        / "application"
        / "market_pipeline"
        / "spot_selection.py"
    ).read_text()
    assert 'if used != "EQ":' in source
    assert 'df["Spot"] = selected' in source


def test_futures_switch_skips_stale_socket_handoff():
    server_dir = Path(__file__).resolve().parents[1] / "server"
    query_source = (server_dir / "websocket_query.py").read_text()
    websocket_source = (server_dir / "websocket.py").read_text()
    assert "futures_switched = True" in query_source
    assert "not query_result.futures_reference_switched" in websocket_source
    assert "self._invalidate_market_baseline()" in query_source


def test_fetch_nifty_futures_parses_nextapi_futidx(monkeypatch):
    payload = {
        "data": [
            {
                "instrumentType": "FUTIDX",
                "identifier": "FUTIDXNIFTY29-09-2026XX0.00",
                "underlying": "NIFTY",
                "expiryDate": "29-Sep-2026",
                "lastPrice": 23744.1,
                "underlyingValue": 23635.1,
                "change": -123.6,
                "pchange": -0.5178,
                "openPrice": 23828,
                "highPrice": 23830,
                "lowPrice": 23725,
                "prevClose": 23867.7,
                "totalTradedVolume": 33466,
                "totalTurnover": 51700204441.6,
                "openInterest": 275179,
            },
            {
                "instrumentType": "OPTIDX",
                "identifier": "OPTION-SHOULD-BE-IGNORED",
                "underlying": "NIFTY",
                "expiryDate": "29-Sep-2026",
                "lastPrice": 100,
            },
        ]
    }

    monkeypatch.setattr(
        market_api,
        "nse_request",
        lambda url, referer=None: payload,
    )

    result = market_api.fetch_nifty_futures("nse50_fut")

    assert len(result) == 1
    row = result.iloc[0]

    assert row["Contract"] == "FUTIDXNIFTY29-09-2026XX0.00"
    assert row["Underlying"] == "NIFTY"
    assert row["LTP"] == 23744.1
    assert row["Spot"] == 23635.1
    assert row["Basis"] == 109.0
    assert row["PrevClose"] == 23867.7
    assert row["Volume"] == 33466
    assert row["Turnover"] == 51700204441.6
    assert row["OI"] == 275179


def test_fetch_nifty_futures_parses_nextapi_futstk(monkeypatch):
    payload = {
        "data": [
            {
                "instrumentType": "FUTSTK",
                "identifier": "FUTSTKRELIANCE29-09-2026XX0.00",
                "underlying": "RELIANCE",
                "expiryDate": "29-Sep-2026",
                "lastPrice": 1296.2,
                "underlyingValue": 1294.9,
                "prevClose": 1300.0,
                "totalTradedVolume": 19326,
                "totalTurnover": 12561030330,
                "openInterest": 257099,
            },
            {
                "instrumentType": "FUTIDX",
                "identifier": "FUTIDXNIFTY29-09-2026XX0.00",
                "underlying": "NIFTY",
                "expiryDate": "29-Sep-2026",
                "lastPrice": 23744.1,
            },
        ]
    }

    monkeypatch.setattr(
        market_api,
        "nse_request",
        lambda url, referer=None: payload,
    )

    result = market_api.fetch_nifty_futures("stock_fut:RELIANCE")

    assert len(result) == 1
    row = result.iloc[0]

    assert row["Contract"] == "FUTSTKRELIANCE29-09-2026XX0.00"
    assert row["Underlying"] == "RELIANCE"
    assert row["LTP"] == 1296.2
    assert row["Spot"] == 1294.9
    assert row["Basis"] == 1.3
    assert row["Volume"] == 19326
    assert row["Turnover"] == 12561030330
    assert row["OI"] == 257099
