import pandas as pd
from datetime import date, timedelta

from market.providers import nse_bse_client as market_api


def test_bse_public_codes_match_exchange_underlying_ids():
    assert market_api.BSE_INDEX_SCRIP_CODES == {
        "SENSEX": "1", "BANKEX": "12", "SENSEX50": "47",
    }


def test_bse_quote_uses_symbol_specific_public_code(monkeypatch):
    seen = []

    def fake_request(url, params):
        seen.append(params["scripcode"])
        return {
            "CurrRate": {"LTP": "25000", "Chg": "100", "PcChg": "0.40"},
            "Header": {"PrevClose": "24900"},
        }

    monkeypatch.setattr(market_api, "bse_request", fake_request)

    assert market_api.fetch_bse_index_quote("BANKEX")["Last Price"] == 25000
    assert market_api.fetch_bse_index_quote("SENSEX50")["Last Price"] == 25000
    assert seen == ["12", "47"]


def test_public_futures_routes_all_bse_indices_by_verified_code(monkeypatch):
    seen = []

    def fake_futures(expiry_str=None, scrip_cd="1"):
        seen.append(scrip_cd)
        expiry = (date.today() + timedelta(days=10)).strftime("%d-%b-%Y")
        return pd.DataFrame([{"Expiry": expiry, "LTP": 25000}])

    monkeypatch.setattr(market_api, "fetch_bse_futures", fake_futures)

    assert not market_api.fetch_public_futures("BANKEX").empty
    assert not market_api.fetch_public_futures("SENSEX50").empty
    assert seen == ["12", "47"]
