from types import SimpleNamespace

import pandas as pd
import pytest

from application.market_pipeline.spot_selection import select_runtime_spot


def _config(price_source, *, broker_enabled=False):
    return SimpleNamespace(
        symbol="NIFTY",
        price_source=price_source,
        broker_enabled=broker_enabled,
    )


def test_forced_futures_replaces_analytics_spot():
    chain = pd.DataFrame([{"Spot": 24000.0}])
    futures = pd.DataFrame([{"LTP": 24125.0}])

    selected, spot, source = select_runtime_spot(
        chain, 24000.0, futures, [], _config("FUT")
    )

    assert spot == 24125.0
    assert source == "FUT"
    assert selected.iloc[0]["Spot"] == 24125.0
    assert chain.iloc[0]["Spot"] == 24000.0


def test_auto_prefers_materially_different_live_cash_quote():
    chain = pd.DataFrame([{"Spot": 24000.0}])
    indices = [{"Symbol": "NIFTY", "Last Price": 24200.0}]

    selected, spot, source = select_runtime_spot(
        chain, 24000.0, None, indices, _config("AUTO", broker_enabled=True)
    )

    assert spot == 24200.0
    assert source == "LIVE_EQ"
    assert selected.iloc[0]["Spot"] == 24200.0


def test_missing_prices_fail_closed():
    with pytest.raises(RuntimeError, match="No usable spot price"):
        select_runtime_spot(
            pd.DataFrame([{"Spot": 0.0}]),
            0.0,
            None,
            [],
            _config("EQ"),
        )
