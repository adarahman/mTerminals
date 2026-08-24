from application.selection_state import MarketSelectionState


def test_market_selection_state_updates_and_snapshots_runtime_selection():
    state = MarketSelectionState(
        symbol="NIFTY",
        expiry=None,
        data_source="NSE_BSE",
    )

    state.select_symbol("BANKNIFTY", "2026-08-27")
    state.select_data_source("UPSTOX")
    state.select_price_source("FUT")
    state.select_futures_expiry("NEXT")

    assert state.snapshot() == {
        "symbol": "BANKNIFTY",
        "expiry": "2026-08-27",
        "data_source": "UPSTOX",
        "price_source": "FUT",
        "futures_expiry": "NEXT",
    }
