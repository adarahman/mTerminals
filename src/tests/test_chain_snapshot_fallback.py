import pandas as pd
import logging

from application.market_pipeline.resources import ChainSnapshotStore


def test_chain_snapshot_fallback_is_bounded_and_copied():
    store = ChainSnapshotStore(
        max_age_seconds=300.0,
        logger=logging.getLogger(__name__),
    )
    key = ("KOTAK", "NIFTY", "01-Sep-2026", "NSE", False)
    original = (pd.DataFrame([{"StrikePrice": 25000}]), 25000.0, "01-Sep-2026", [])

    store.remember(key, original, now=100.0)
    timings = {}
    cached = store.load(
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

    assert store.load(
        key,
        source="KOTAK",
        now=401.0,
    ) is None
