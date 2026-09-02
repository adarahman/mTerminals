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


def test_velocity_bridges_provider_when_boundary_oi_is_identical(tmp_path, monkeypatch):
    log_path = tmp_path / "velocity.parquet"
    monkeypatch.setattr(oi_analysis, "_HISTORY_MEM", DirtyFrameStore())
    now = pd.Timestamp.now()
    old = _snapshot(now - timedelta(minutes=5))
    old["Provider"] = "SMARTAPI"
    boundary_old = _snapshot(now - timedelta(minutes=1))
    boundary_old["Provider"] = "SMARTAPI"
    boundary_new = _snapshot(now - timedelta(seconds=59))
    boundary_new["Provider"] = "UPSTOX"
    oi_analysis.append_json_history(
        pd.concat([old, boundary_old, boundary_new], ignore_index=True),
        log_path=str(log_path), flush_interval_seconds=3600,
    )

    current = pd.DataFrame([{
        "StrikePrice": 24500, "Expiry": "2026-08-13",
        "CE_OI": 125, "PE_OI": 150, "CE_LTP": 9, "PE_LTP": 13,
    }])
    result = oi_analysis.get_oi_velocity(
        current, "NIFTY", "2026-08-13", windows=(5,), lot_size=1,
        log_path=str(log_path), provider="UPSTOX",
    )

    assert len(result) == 1
    assert result.iloc[0]["ceDOI"] > 0
    assert result.iloc[0]["peDOI"] > 0


def test_provider_boundary_allows_small_market_movement():
    older_rows = []
    newer_rows = []
    for offset in range(10):
        strike = 24000 + offset * 50
        older_rows.append({"StrikePrice": strike, "CE_OI": 1000 + offset * 100,
                           "PE_OI": 1200 + offset * 100})
        newer_rows.append({"StrikePrice": strike, "CE_OI": 1005 + offset * 100,
                           "PE_OI": 1195 + offset * 100})

    assert oi_analysis._oi_snapshots_match(
        pd.DataFrame(older_rows), pd.DataFrame(newer_rows)
    )


def test_provider_boundary_allows_a_wider_strike_range():
    narrower = pd.DataFrame([
        {"StrikePrice": 24500, "CE_OI": 1000, "PE_OI": 1200},
        {"StrikePrice": 24550, "CE_OI": 900, "PE_OI": 1300},
    ])
    wider = pd.concat([
        narrower,
        pd.DataFrame([
            {"StrikePrice": 24450, "CE_OI": 500, "PE_OI": 700},
            {"StrikePrice": 24600, "CE_OI": 600, "PE_OI": 800},
        ]),
    ], ignore_index=True)

    assert oi_analysis._oi_snapshots_match(narrower, wider)


def test_velocity_resets_when_provider_boundary_oi_differs(tmp_path, monkeypatch):
    log_path = tmp_path / "velocity.parquet"
    monkeypatch.setattr(oi_analysis, "_HISTORY_MEM", DirtyFrameStore())
    now = pd.Timestamp.now()
    old = _snapshot(now - timedelta(minutes=5))
    old["Provider"] = "SMARTAPI"
    boundary_old = _snapshot(now - timedelta(minutes=1))
    boundary_old["Provider"] = "SMARTAPI"
    boundary_new = _snapshot(now - timedelta(seconds=59))
    boundary_new["Provider"] = "UPSTOX"
    # Simulate a contracts-vs-underlying-quantity unit mismatch.
    boundary_new["CE_OI"] = 5000
    boundary_new["PE_OI"] = 6000
    oi_analysis.append_json_history(
        pd.concat([old, boundary_old, boundary_new], ignore_index=True),
        log_path=str(log_path), flush_interval_seconds=3600,
    )

    current = pd.DataFrame([{
        "StrikePrice": 24500, "Expiry": "2026-08-13",
        "CE_OI": 125, "PE_OI": 150, "CE_LTP": 9, "PE_LTP": 13,
    }])
    result = oi_analysis.get_oi_velocity(
        current, "NIFTY", "2026-08-13", windows=(5,), lot_size=1,
        log_path=str(log_path), provider="UPSTOX",
    )

    assert result.empty
