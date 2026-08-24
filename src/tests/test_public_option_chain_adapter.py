import pandas as pd

from market.providers.option_chain import PublicOptionChainAdapter


def test_public_bse_adapter_normalizes_chain(monkeypatch):
    raw = pd.DataFrame({"Strike": [81000], "CE_LTP": [100.0]})
    monkeypatch.setattr(
        "market.providers.option_chain.nse_bse_client.fetch_bse_json_options",
        lambda expiry, scrip_cd: (raw, 80950.0),
    )

    frame = PublicOptionChainAdapter().fetch_bse_chain(
        "SENSEX", "27-Aug-2026"
    )

    assert frame["StrikePrice"].iloc[0] == 81000
    assert frame["Spot"].iloc[0] == 80950.0
    assert frame["Symbol"].iloc[0] == "SENSEX"
    assert frame["PE_BuyQty"].iloc[0] == 0


def test_public_adapter_delegates_nse_payload(monkeypatch):
    expected = {"records": {"data": []}}
    monkeypatch.setattr(
        "market.providers.option_chain.nse_bse_client.fetch_option_chain",
        lambda symbol, expiry: expected,
    )

    assert PublicOptionChainAdapter().fetch_nse_payload(
        "NIFTY", "25-Aug-2026"
    ) is expected
