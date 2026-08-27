import pandas as pd

from application import option_chain_runtime as runtime


def test_chain_snapshot_fallback_is_bounded_and_copied(monkeypatch):
    monkeypatch.setattr(runtime, "_chain_snapshot_cache", {})
    key = ("KOTAK", "NIFTY", "01-Sep-2026", "NSE", False)
    original = (pd.DataFrame([{"StrikePrice": 25000}]), 25000.0, "01-Sep-2026", [])

    runtime._remember_chain_snapshot(key, original, now=100.0)
    timings = {}
    cached = runtime._load_chain_snapshot(
        key,
        source="KOTAK",
        timings=timings,
        now=110.0,
    )

    assert cached is not original
    assert cached[0].equals(original[0])
    cached[0].loc[0, "StrikePrice"] = 99999
    assert original[0].loc[0, "StrikePrice"] == 25000
    assert timings["chainStale"] == 1
    assert timings["chainStaleAgeSeconds"] == 10.0
    assert "10.0s-old KOTAK snapshot" in timings["chainStaleReason"]

    assert runtime._load_chain_snapshot(
        key,
        source="KOTAK",
        now=100.0 + runtime._CHAIN_FALLBACK_MAX_AGE_SECONDS + 1,
    ) is None
