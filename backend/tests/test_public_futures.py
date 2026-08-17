from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import market_api


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
    source = (Path(__file__).resolve().parents[1] / "option_chain_json.py").read_text()
    assert 'spot = fut_ltp' not in source
    assert 'never replace df["Spot"]' in source


def test_futures_switch_skips_stale_socket_handoff():
    source = (Path(__file__).resolve().parents[2] / "ws_server_live.py").read_text()
    assert "futures_reference_switched = True" in source
    assert "LAST_PAYLOAD is not None and not futures_reference_switched" in source
    assert "_LAST_SENT = None" in source
