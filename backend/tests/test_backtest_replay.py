"""Tests for backtest/replay.py.

Seeds a throwaway decision_snapshots SQLite db (same schema
snapshot_logger.py writes) and a throwaway oi_history_log-shaped
parquet, then runs the replay engine against them — verifying it
actually drives the real AutoExecutor (gating behaves identically to
test_auto_executor.py's expectations) and computes correct fills/PnL
from the seeded LTP series.
"""

import json
import os
import sqlite3

import pandas as pd
import pytest

from backtest.replay import run_backtest_sync
from backtest.snapshot_logger import _ensure_schema


def _decision(action_type="BUY_CE", strike=20000, confidence=70,
              conflict=False, execute=True):
    d = {
        "bias": "BULLISH", "biasStrength": "MODERATE", "confidence": confidence,
        "conflictFlag": conflict, "action": action_type, "actionType": action_type,
        "suggestedStrike": strike, "suggestedStrategy": "", "executeRecommended": execute,
        "strategyCaution": "", "activeSignals": [], "verdicts": {}, "oiAnnotations": {},
        "autoStrategy": {}, "_debug": {},
    }
    return d


def _seed_snapshots(db_path, symbol, rows):
    """rows: list of (snapshot_time_iso, expiry, decision_dict)."""
    _ensure_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        for ts, expiry, decision in rows:
            conn.execute(
                "INSERT INTO decision_snapshots (snapshot_time, symbol, expiry, dte, "
                "spot, atm, strike_step, lot_size, india_vix, bias, bias_strength, "
                "confidence, conflict_flag, action_type, suggested_strike, "
                "execute_recommended, strategy_caution, decision_json) "
                "VALUES (?, ?, ?, 5, 20000, 20000, 50, 75, 15.0, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, symbol, expiry, decision["bias"], decision["biasStrength"],
                 decision["confidence"], int(decision["conflictFlag"]), decision["actionType"],
                 decision["suggestedStrike"], int(decision["executeRecommended"]),
                 decision["strategyCaution"], json.dumps(decision)),
            )


def _seed_ltp(path, symbol, expiry, strike, series):
    """series: list of (snapshot_time_iso, ce_ltp, pe_ltp)."""
    rows = [
        {"snapshot_time": ts, "Symbol": symbol, "Expiry": expiry, "StrikePrice": strike,
         "CE_LTP": ce, "PE_LTP": pe}
        for ts, ce, pe in series
    ]
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_entry_and_opposite_signal_exit_computes_correct_pnl(tmp_path):
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")

    rows = [
        ("2026-08-01T09:20:00", "07AUG2026", _decision(action_type="BUY_CE", strike=20000)),
        ("2026-08-01T09:30:00", "07AUG2026", _decision(action_type="SELL_CE", strike=20000)),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)
    _seed_ltp(ltp_path, "NIFTY", "07AUG2026", 20000, [
        ("2026-08-01T09:20:00", 100.0, 80.0),
        ("2026-08-01T09:30:00", 130.0, 60.0),
    ])

    result = run_backtest_sync("NIFTY", db_path=db_path, ltp_log_path=ltp_path)

    assert len(result.closed_trades) == 1
    trade = result.closed_trades[0]
    assert trade.side == "BUY"
    assert trade.instrument_type == "CE"
    assert trade.entry_price == 100.0
    assert trade.exit_price == 130.0
    assert trade.exit_reason == "opposite_signal"
    # (130 - 100) * lot_size(75) * qty_lots(1)
    assert trade.pnl == 2250.0


def test_day_boundary_forces_square_off(tmp_path):
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")

    rows = [
        ("2026-08-01T15:20:00", "07AUG2026", _decision(action_type="BUY_CE", strike=20000)),
        ("2026-08-02T09:15:00", "07AUG2026", _decision(action_type="WAIT")),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)
    _seed_ltp(ltp_path, "NIFTY", "07AUG2026", 20000, [
        ("2026-08-01T15:20:00", 100.0, 80.0),
        ("2026-08-01T15:25:00", 110.0, 70.0),  # last price seen before day rolls over
        ("2026-08-02T09:15:00", 200.0, 10.0),  # next day — should NOT be used as exit fill
    ])

    result = run_backtest_sync("NIFTY", db_path=db_path, ltp_log_path=ltp_path)

    assert len(result.closed_trades) == 1
    trade = result.closed_trades[0]
    assert trade.exit_reason == "day_boundary_square_off"
    assert trade.exit_price == 110.0  # last known price BEFORE the day boundary, not the new day's


def test_low_confidence_signal_never_enters(tmp_path):
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")

    rows = [
        ("2026-08-01T09:20:00", "07AUG2026", _decision(action_type="BUY_CE", confidence=10)),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)
    _seed_ltp(ltp_path, "NIFTY", "07AUG2026", 20000, [("2026-08-01T09:20:00", 100.0, 80.0)])

    result = run_backtest_sync("NIFTY", db_path=db_path, ltp_log_path=ltp_path, min_confidence=40)

    assert result.trades == []


def test_conflict_flag_blocks_entry_same_as_live(tmp_path):
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")

    rows = [
        ("2026-08-01T09:20:00", "07AUG2026", _decision(action_type="BUY_CE", conflict=True)),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)
    _seed_ltp(ltp_path, "NIFTY", "07AUG2026", 20000, [("2026-08-01T09:20:00", 100.0, 80.0)])

    result = run_backtest_sync("NIFTY", db_path=db_path, ltp_log_path=ltp_path)

    assert result.trades == []


def test_missing_ltp_data_marks_unpriced_not_fabricated(tmp_path):
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")

    rows = [
        ("2026-08-01T09:20:00", "07AUG2026", _decision(action_type="BUY_CE", strike=99999)),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)
    # LTP data exists, but not for strike 99999.
    _seed_ltp(ltp_path, "NIFTY", "07AUG2026", 20000, [("2026-08-01T09:20:00", 100.0, 80.0)])

    result = run_backtest_sync("NIFTY", db_path=db_path, ltp_log_path=ltp_path)

    assert result.trades == []
    assert result.unpriced_signals == 1


def test_cooldown_prevents_reentry_immediately_after_exit(tmp_path):
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")

    rows = [
        ("2026-08-01T09:20:00", "07AUG2026", _decision(action_type="BUY_CE", strike=20000)),
        ("2026-08-01T09:22:00", "07AUG2026", _decision(action_type="SELL_CE", strike=20000)),
        ("2026-08-01T09:23:00", "07AUG2026", _decision(action_type="BUY_CE", strike=20000)),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)
    _seed_ltp(ltp_path, "NIFTY", "07AUG2026", 20000, [
        ("2026-08-01T09:20:00", 100.0, 80.0),
        ("2026-08-01T09:22:00", 110.0, 70.0),
        ("2026-08-01T09:23:00", 111.0, 69.0),
    ])

    result = run_backtest_sync("NIFTY", db_path=db_path, ltp_log_path=ltp_path,
                                cooldown_seconds=300)

    # Only the first entry+exit should have filled; the re-entry attempt
    # 180s after the original ENTRY (09:20 -> 09:23) is still inside the
    # 300s cooldown AutoExecutor measures from last execution time.
    assert len(result.closed_trades) == 1


def test_empty_history_returns_empty_result(tmp_path):
    db_path = str(tmp_path / "snapshots.db")
    _ensure_schema(db_path)
    result = run_backtest_sync("NIFTY", db_path=db_path,
                                ltp_log_path=str(tmp_path / "missing.parquet"))
    assert result.trades == []
    assert result.summary()["num_trades"] == 0
