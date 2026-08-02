"""
backtest/snapshot_logger.py
----------------------------
Captures, tick by tick, exactly what decision_engine.py decided — so that
backtest/replay.py has real history to replay auto_executor.py against
instead of none.

Why this exists (read this before deleting it as "just logging"):
Before this module, NOTHING persisted the inputs decision_engine.py's
evaluate() needs (spot, india_vix, basis, fut_signal, combined_view bias,
pcr, max_pain, ...) or its output (bias/confidence/action_type/
suggestedStrike) anywhere beyond the current in-memory tick. The one
existing history store — oi/oi_analysis.py's oi_history_log.parquet —
only has per-strike CE/PE OI/LTP/Volume/IV, which is enough to
mark-to-market a position once you know when it opened and at what
strike, but has no spot/VIX/basis/bias, so it alone cannot reconstruct
what the DecisionEngine would have said on any past tick. There was
therefore no way to backtest decision_engine.py against history — only
against ticks captured from the moment this module started running.
This module is that starting point. It does not backfill the past
(can't — the data never existed); it makes today's data exist for
tomorrow's backtest.

What's captured: the ALREADY-COMPUTED DecisionResult dict (the same
dict payload["decision"] in mTerminals_json.py sends to the dashboard),
not a fresh re-run of DecisionEngine — replay.py doesn't need to
recompute the decision, only to know what it WAS and feed it through
AutoExecutor.evaluate() exactly as production does, gated by whatever
AutoExecutor config the backtest run is testing. Re-deriving the
decision from raw chain snapshots is a separate, much heavier effort
(would mean persisting full df/df_clean/df_idx/df_fut/vel_df every
tick) that isn't needed for the actual goal here: validating
auto_executor.py's gating + P&L outcome against real decision history.

Storage: SQLite (not parquet) — this is one row per tick per symbol
(order of magnitude fewer writes than oi_history_log's per-strike
rows), so the DirtyFrameStore in-memory-batching oi_history_log needed
for its 5s-cadence per-strike write volume isn't necessary here. Same
CACHE_DIR convention as risk/account_guard.py's live_risk_guard.db.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime

from paths import CACHE_DIR

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(CACHE_DIR, "decision_snapshot_log.db")

_lock = threading.Lock()
_schema_ready_for: set[str] = set()


def _ensure_schema(db_path: str):
    if db_path in _schema_ready_for:
        return
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_snapshots (
                snapshot_time      TEXT NOT NULL,
                symbol             TEXT NOT NULL,
                expiry             TEXT,
                dte                INTEGER,
                spot               REAL,
                atm                REAL,
                strike_step        INTEGER,
                lot_size           INTEGER,
                india_vix          REAL,
                bias               TEXT,
                bias_strength      TEXT,
                confidence         INTEGER,
                conflict_flag      INTEGER,
                action_type        TEXT,
                suggested_strike   INTEGER,
                execute_recommended INTEGER,
                strategy_caution   TEXT,
                decision_json      TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_decision_snapshots_symbol_time "
            "ON decision_snapshots (symbol, snapshot_time)"
        )
    _schema_ready_for.add(db_path)


def log_decision_snapshot(engine_result, decision_dict: dict, db_path: str = DB_PATH) -> None:
    """Call once per tick, right after payload["decision"] is computed in
    mTerminals_json.py's export_dashboard_json() — engine_result is the
    same EngineResult already passed into DecisionEngine().evaluate(),
    decision_dict is that call's .to_dict() output. Never raises: a
    logging failure must not take down the live tick pipeline (same
    posture as account_guard.py's kill-switch write failure — logged
    loudly, but doesn't propagate).
    """
    if engine_result is None or decision_dict is None:
        return
    try:
        _ensure_schema(db_path)
        row = (
            datetime.now().isoformat(timespec="seconds"),
            str(getattr(engine_result, "symbol", "")),
            str(getattr(engine_result, "expiry", "")),
            int(getattr(engine_result, "dte", 0) or 0),
            float(getattr(engine_result, "spot", 0.0) or 0.0),
            float(getattr(engine_result, "atm", 0.0) or 0.0),
            int(getattr(engine_result, "strike_step", 0) or 0),
            int(getattr(engine_result, "lot_size", 0) or 0),
            float(getattr(engine_result, "india_vix", 0.0) or 0.0),
            decision_dict.get("bias"),
            decision_dict.get("biasStrength"),
            int(decision_dict.get("confidence", 0) or 0),
            int(bool(decision_dict.get("conflictFlag"))),
            decision_dict.get("actionType"),
            decision_dict.get("suggestedStrike"),
            int(bool(decision_dict.get("executeRecommended"))),
            decision_dict.get("strategyCaution") or "",
            json.dumps(decision_dict, default=str),
        )
        with _lock, sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO decision_snapshots (snapshot_time, symbol, expiry, dte, "
                "spot, atm, strike_step, lot_size, india_vix, bias, bias_strength, "
                "confidence, conflict_flag, action_type, suggested_strike, "
                "execute_recommended, strategy_caution, decision_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
    except Exception as e:
        # Logged, never raised — see docstring.
        logger.warning(f"[snapshot_logger] could not log decision snapshot: {e}")


def load_decision_snapshots(symbol: str, start: str | None = None, end: str | None = None,
                             db_path: str = DB_PATH) -> list[dict]:
    """Chronological list of dict rows for `symbol`, optionally bounded by
    ISO timestamp strings [start, end). Used by backtest/replay.py."""
    _ensure_schema(db_path)
    query = "SELECT * FROM decision_snapshots WHERE symbol = ?"
    params: list = [symbol]
    if start:
        query += " AND snapshot_time >= ?"
        params.append(start)
    if end:
        query += " AND snapshot_time < ?"
        params.append(end)
    query += " ORDER BY snapshot_time ASC"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]
