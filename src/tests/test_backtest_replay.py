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


def test_summary_by_confidence_bucket_captures_confidence_at_entry(tmp_path):
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")

    rows = [
        # Trade 1: enters at 45 confidence (low bucket), wins.
        ("2026-08-01T09:20:00", "07AUG2026",
         _decision(action_type="BUY_CE", strike=20000, confidence=45)),
        ("2026-08-01T09:30:00", "07AUG2026",
         _decision(action_type="SELL_CE", strike=20000, confidence=50)),
        # Trade 2: enters at 80 confidence (high bucket), also wins —
        # separate strike so the two trades don't collide.
        ("2026-08-01T09:40:00", "07AUG2026",
         _decision(action_type="BUY_PE", strike=20100, confidence=80)),
        ("2026-08-01T09:50:00", "07AUG2026",
         _decision(action_type="SELL_PE", strike=20100, confidence=75)),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)
    # Two strikes' worth of ticks in one parquet — _seed_ltp writes a single
    # strike per call and would overwrite the file, so build it directly.
    pd.DataFrame([
        {"snapshot_time": "2026-08-01T09:20:00", "Symbol": "NIFTY", "Expiry": "07AUG2026",
         "StrikePrice": 20000, "CE_LTP": 100.0, "PE_LTP": 80.0},
        {"snapshot_time": "2026-08-01T09:30:00", "Symbol": "NIFTY", "Expiry": "07AUG2026",
         "StrikePrice": 20000, "CE_LTP": 130.0, "PE_LTP": 60.0},
        {"snapshot_time": "2026-08-01T09:40:00", "Symbol": "NIFTY", "Expiry": "07AUG2026",
         "StrikePrice": 20100, "CE_LTP": 90.0, "PE_LTP": 50.0},
        {"snapshot_time": "2026-08-01T09:50:00", "Symbol": "NIFTY", "Expiry": "07AUG2026",
         "StrikePrice": 20100, "CE_LTP": 70.0, "PE_LTP": 65.0},
    ]).to_parquet(ltp_path, index=False)

    result = run_backtest_sync("NIFTY", db_path=db_path, ltp_log_path=ltp_path)
    assert len(result.closed_trades) == 2
    assert {t.confidence for t in result.closed_trades} == {45, 80}

    buckets = result.summary_by_confidence_bucket()
    by_range = {b["range"]: b for b in buckets}
    assert by_range["40-54"]["num_trades"] == 1
    assert by_range["40-54"]["win_rate"] == 1.0
    assert by_range["70-84"]["num_trades"] == 1
    assert by_range["70-84"]["win_rate"] == 1.0
    # Buckets with no trades still report, just empty — makes gaps visible
    # instead of silently omitting them from the printed breakdown.
    assert by_range["55-69"]["num_trades"] == 0
    assert by_range["55-69"]["win_rate"] is None


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


def test_day_boundary_ignores_stale_price_across_logging_gap(tmp_path):
    """Regression test: if the decision-snapshot log has a gap (server
    wasn't running) so the next logged row for this symbol lands TWO
    calendar days after entry, last_price_before() must not reach past
    entry's own day and grab a tick from the day AFTER entry — that
    produces a nonsensical fill (e.g. next morning's open price) mislabeled
    as a same-day close. With no in-session tick available, the trade
    should come back unpriced instead of fabricating an exit."""
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")

    rows = [
        ("2026-08-05T12:18:00", "07AUG2026", _decision(action_type="BUY_CE", strike=20000)),
        # gap: nothing logged for the rest of 08-05 or all of 08-06
        ("2026-08-07T09:15:00", "07AUG2026", _decision(action_type="WAIT")),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)
    _seed_ltp(ltp_path, "NIFTY", "07AUG2026", 20000, [
        ("2026-08-05T12:18:00", 102.9, 80.0),
        ("2026-08-06T09:42:00", 122.5, 60.0),  # next day's tick — must NOT be used as the exit fill
    ])

    result = run_backtest_sync("NIFTY", db_path=db_path, ltp_log_path=ltp_path)

    assert len(result.trades) == 1
    trade = result.trades[0]
    # No tick after entry exists within entry's own day (08-05) — the
    # 08-06 tick must be rejected as out-of-session. Falls back to the
    # entry's own fill as the last known in-session price (flat exit),
    # which is honest about the data gap instead of fabricating a loss
    # from the next day's open.
    assert trade.exit_price == 102.9
    assert trade.pnl == 0.0
    assert trade.exit_reason == "day_boundary_square_off"


def test_day_boundary_ignores_after_hours_tick(tmp_path):
    """Regression test: an LTP tick logged after the real market close
    (e.g. server left running / dev session past 15:30) must not be used
    as the day-boundary settlement price, even though it's on the same
    calendar day as entry — this was reproducing Mo's actual trade 2
    (23:10 PM tick used as an intraday close)."""
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")

    rows = [
        ("2026-08-05T12:18:00", "07AUG2026", _decision(action_type="BUY_CE", strike=20000)),
        ("2026-08-06T09:15:00", "07AUG2026", _decision(action_type="WAIT")),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)
    _seed_ltp(ltp_path, "NIFTY", "07AUG2026", 20000, [
        ("2026-08-05T12:18:00", 102.9, 80.0),
        ("2026-08-05T14:50:00", 90.3, 65.0),   # real intraday tick, within session
        ("2026-08-05T23:10:00", 122.5, 60.0),  # after-hours artifact — must NOT be used
    ])

    result = run_backtest_sync("NIFTY", db_path=db_path, ltp_log_path=ltp_path)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_price == 90.3
    assert trade.exit_reason == "day_boundary_square_off"


def test_after_hours_decision_never_enters(tmp_path):
    """Regression test: a decision row logged outside the real trading
    session (09:15-15:30) must not open a position — live, the broker WS
    has no ticks after close, so no order could ever fire then. This was
    reproducing Mo's actual trade 1 (entered 16:37 IST, an hour past
    close, likely from a dev/test session left running)."""
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")

    rows = [
        ("2026-08-04T16:37:36", "07AUG2026", _decision(action_type="SELL_CE", strike=24650)),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)
    _seed_ltp(ltp_path, "NIFTY", "07AUG2026", 24650, [
        ("2026-08-04T16:37:36", 446.0, 300.0),
    ])

    result = run_backtest_sync("NIFTY", db_path=db_path, ltp_log_path=ltp_path)

    assert result.trades == []


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


def test_signal_does_not_fill_from_a_much_later_quote(tmp_path):
    db_path = str(tmp_path / "snapshots.db")
    ltp_path = str(tmp_path / "ltp.parquet")
    _seed_snapshots(db_path, "NIFTY", [
        ("2026-08-01T09:20:00", "07AUG2026", _decision(action_type="BUY_CE", strike=20000)),
    ])
    _seed_ltp(ltp_path, "NIFTY", "07AUG2026", 20000, [
        ("2026-08-01T10:20:00", 100.0, 80.0),
    ])

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
    assert result.metadata()["snapshotCount"] == 0
    assert result.metadata()["transactionCostsIncluded"] is False
    assert result.metadata()["slippageIncluded"] is False


def test_result_reports_actual_snapshot_coverage_and_model_assumptions(tmp_path):
    db_path = str(tmp_path / "snapshots.db")
    rows = [
        ("2026-08-01T09:20:00", "07AUG2026", _decision(action_type="WAIT")),
        ("2026-08-01T09:25:00", "07AUG2026", _decision(action_type="WAIT")),
    ]
    _seed_snapshots(db_path, "NIFTY", rows)

    result = run_backtest_sync(
        "NIFTY", start="2026-08-01", end="2026-08-02", db_path=db_path,
        ltp_log_path=str(tmp_path / "missing.parquet"),
    )
    metadata = result.metadata()

    assert metadata["snapshotCount"] == 2
    assert metadata["dataStart"] == "2026-08-01T09:20:00"
    assert metadata["dataEnd"] == "2026-08-01T09:25:00"
    assert metadata["requestedStart"] == "2026-08-01"
    assert metadata["decisionScoringRecomputed"] is False
