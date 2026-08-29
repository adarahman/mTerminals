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
