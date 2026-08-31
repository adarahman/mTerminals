from oi.futures_oi_tracker import FuturesOITracker


def test_futures_oi_baseline_survives_restart(tmp_path):
    path = tmp_path / "futures_oi.json"
    first = FuturesOITracker(str(path))
    assert first.update("NIFTY-FUT", 1000)["fut_oi_chg"] == 0
    assert first.update("NIFTY-FUT", 1100)["fut_oi_chg"] == 100

    restarted = FuturesOITracker(str(path))
    # DailyMarketScheduler invokes reset_sessions once when a new process
    # starts. A same-day call must retain the baseline loaded above.
    assert restarted.reset_session() is False
    result = restarted.update("NIFTY-FUT", 1150)
    assert result["fut_oi_chg"] == 150
    assert result["fut_oi_chg_pct"] == 15
