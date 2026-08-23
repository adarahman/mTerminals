import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from backtest import snapshot_logger
from oi import oi_analysis
from storage.caches import DirtyFrameStore


def test_velocity_history_prunes_stale_rows_during_initial_load(tmp_path, monkeypatch):
    path = tmp_path / "oi_velocity_history.parquet"
    now = pd.Timestamp.now()
    pd.DataFrame([
        {"Symbol": "NIFTY", "Expiry": "10-Aug-2026", "StrikePrice": 24000,
         "snapshot_time": now - pd.Timedelta(hours=8)},
        {"Symbol": "NIFTY", "Expiry": "10-Aug-2026", "StrikePrice": 24100,
         "snapshot_time": now - pd.Timedelta(minutes=5)},
    ]).to_parquet(path, index=False)
    monkeypatch.setattr(oi_analysis, "JSON_HISTORY_LOG_PATH", str(path))
    monkeypatch.setattr(oi_analysis, "_HISTORY_MEM", DirtyFrameStore())

    previous = oi_analysis.read_last_json_snapshot("NIFTY", log_path=str(path))

    assert previous["StrikePrice"].tolist() == [24100]
    assert len(oi_analysis._HISTORY_MEM.df) == 1
    assert oi_analysis._HISTORY_MEM.dirty is True


def test_decision_snapshot_log_prunes_rows_outside_retention(tmp_path, monkeypatch):
    path = str(tmp_path / "decision.db")
    snapshot_logger._ensure_schema(path)
    old_time = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO decision_snapshots "
            "(snapshot_time, symbol, decision_json) VALUES (?, ?, ?)",
            (old_time, "NIFTY", "{}"),
        )

    monkeypatch.setattr(snapshot_logger, "RETENTION_DAYS", 45)
    snapshot_logger._last_pruned_at.pop(path, None)
    engine = SimpleNamespace(
        symbol="NIFTY", expiry="10-Aug-2026", dte=1, spot=24000,
        atm=24000, strike_step=50, lot_size=75, india_vix=13,
    )
    snapshot_logger.log_decision_snapshot(
        engine,
        {"bias": "Bullish", "confidence": 50, "actionType": "BUY_CE"},
        db_path=path,
    )

    rows = snapshot_logger.load_decision_snapshots("NIFTY", db_path=path)
    assert len(rows) == 1
    assert rows[0]["snapshot_time"] != old_time

