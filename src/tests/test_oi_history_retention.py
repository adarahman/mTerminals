from datetime import timedelta

import pandas as pd

from oi import oi_analysis
from storage.caches import DirtyFrameStore


def _snapshot(observed_at, strike=24500):
    return pd.DataFrame(
        [
            {
                "snapshot_time": pd.Timestamp(observed_at),
                "Symbol": "NIFTY",
                "StrikePrice": strike,
                "Expiry": "2026-08-13",
                "CE_OI": 100,
                "PE_OI": 120,
                "CE_LTP": 10.0,
                "PE_LTP": 12.0,
            }
        ]
    )


def test_live_velocity_history_is_bounded_to_recent_window(tmp_path, monkeypatch):
    log_path = tmp_path / "velocity.parquet"
    monkeypatch.setattr(oi_analysis, "_HISTORY_MEM", DirtyFrameStore())
    now = pd.Timestamp.now()

    oi_analysis.append_json_history(
        pd.concat(
            [
                _snapshot(now - timedelta(minutes=36)),
                _snapshot(now - timedelta(minutes=34), strike=24550),
            ],
            ignore_index=True,
        ),
        log_path=str(log_path),
        flush_interval_seconds=3600,
    )

    retained = oi_analysis._HISTORY_MEM.df
    assert len(retained) == 1
    assert retained.iloc[0]["StrikePrice"] == 24550


def test_live_store_does_not_reuse_legacy_archive_path():
    assert oi_analysis.JSON_HISTORY_LOG_PATH != oi_analysis.LEGACY_OI_HISTORY_LOG_PATH
    assert oi_analysis.JSON_HISTORY_LOG_PATH.endswith("oi_velocity_history.parquet")


def test_velocity_never_compares_different_brokers(tmp_path, monkeypatch):
    log_path = tmp_path / "velocity.parquet"
    monkeypatch.setattr(oi_analysis, "_HISTORY_MEM", DirtyFrameStore())
    old = _snapshot(pd.Timestamp.now() - timedelta(minutes=5))
    old["Provider"] = "BREEZE"
    oi_analysis.append_json_history(
        old, log_path=str(log_path), flush_interval_seconds=3600
    )
    current = pd.DataFrame([{
        "StrikePrice": 24500, "Expiry": "2026-08-13",
        "CE_OI": 500, "PE_OI": 600, "CE_LTP": 9, "PE_LTP": 13,
    }])
    result = oi_analysis.get_oi_velocity(
        current, "NIFTY", "2026-08-13", windows=(5,), lot_size=1,
        log_path=str(log_path), provider="KOTAK",
    )
    assert result.empty
