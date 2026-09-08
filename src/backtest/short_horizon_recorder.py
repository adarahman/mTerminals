"""Persistence and outcome resolution for short-horizon move observations.

This module is observation-only. It records the already-computed
DecisionResult.short_horizon prediction and later attaches the actual
spot outcome at +1m, +2m and +3m using the existing minute-level history.

It must never influence confidence, action, strategy selection, or execution.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from math import isfinite

from analytics.oversold_oi_support import recent_spot_closes
from backtest.snapshot_logger import (
    DB_PATH,
    PRUNE_INTERVAL_SECONDS,
    RETENTION_DAYS,
)

logger = logging.getLogger(__name__)

_lock = __import__("threading").Lock()
_last_pruned_at: dict[str, datetime] = {}
_schema_ready_for: set[str] = set()


def _minute(timestamp: str) -> int | None:
    try:
        return int(
            datetime.fromisoformat(
                str(timestamp).replace("Z", "+00:00")
            ).timestamp()
            // 60
        )
    except (TypeError, ValueError):
        return None


def _direction_from_move(move_pct: float) -> str:
    if move_pct > 0:
        return "UP"
    if move_pct < 0:
        return "DOWN"
    return "FLAT"


def _hit(predicted: str, actual: str) -> int | None:
    """Return hit/miss only for directional predictions.

    NEUTRAL predictions are deliberately excluded from directional
    accuracy statistics rather than being counted as misses.
    """
    if predicted not in {"UP", "DOWN"}:
        return None
    return int(predicted == actual)


def _ensure_schema(db_path: str) -> None:
    if db_path in _schema_ready_for:
        return

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS short_horizon_observations (
                observation_time       TEXT NOT NULL,
                observation_minute     INTEGER NOT NULL,
                symbol                 TEXT NOT NULL,
                spot                   REAL NOT NULL,

                direction              TEXT NOT NULL,
                state                  TEXT NOT NULL,
                score                  REAL NOT NULL,
                move_likelihood        INTEGER NOT NULL,
                velocity               REAL NOT NULL,
                acceleration           REAL NOT NULL,
                oi_score               REAL NOT NULL,
                futures_score          REAL NOT NULL,
                compression_active     INTEGER NOT NULL,

                outcome_1m_spot        REAL,
                outcome_1m_pct         REAL,
                outcome_1m_direction   TEXT,
                hit_1m                INTEGER,

                outcome_2m_spot        REAL,
                outcome_2m_pct         REAL,
                outcome_2m_direction   TEXT,
                hit_2m                INTEGER,

                outcome_3m_spot        REAL,
                outcome_3m_pct         REAL,
                outcome_3m_direction   TEXT,
                hit_3m                INTEGER,

                resolved_1m            INTEGER NOT NULL DEFAULT 0,
                resolved_2m            INTEGER NOT NULL DEFAULT 0,
                resolved_3m            INTEGER NOT NULL DEFAULT 0,

                prediction_json       TEXT NOT NULL,

                UNIQUE(symbol, observation_minute)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_short_horizon_symbol_time
            ON short_horizon_observations(symbol, observation_minute)
            """
        )

    _schema_ready_for.add(db_path)


def _resolve_pending(
    conn: sqlite3.Connection,
    symbol: str,
) -> None:
    """Resolve every pending exact-minute outcome currently available."""

    rows = conn.execute(
        """
        SELECT
            observation_minute,
            spot,
            direction,
            resolved_1m,
            resolved_2m,
            resolved_3m
        FROM short_horizon_observations
        WHERE symbol = ?
          AND (
              resolved_1m = 0
              OR resolved_2m = 0
              OR resolved_3m = 0
          )
        ORDER BY observation_minute
        """,
        (symbol,),
    ).fetchall()

    if not rows:
        return

    closes = dict(recent_spot_closes(symbol, max_points=120))
    if not closes:
        return

    for (
        observation_minute,
        signal_spot,
        direction,
        resolved_1m,
        resolved_2m,
        resolved_3m,
    ) in rows:
        updates: dict[str, object] = {}

        for horizon, resolved in (
            (1, resolved_1m),
            (2, resolved_2m),
            (3, resolved_3m),
        ):
            if resolved:
                continue

            target_minute = observation_minute + horizon
            target_spot = closes.get(target_minute)

            # Missing exact market minute stays pending.
            if target_spot is None:
                continue

            try:
                target_spot = float(target_spot)
                signal_spot_float = float(signal_spot)
            except (TypeError, ValueError):
                continue

            if (
                not isfinite(target_spot)
                or not isfinite(signal_spot_float)
                or signal_spot_float <= 0
            ):
                continue

            move_pct = (
                (target_spot - signal_spot_float)
                / signal_spot_float
            ) * 100.0
            actual_direction = _direction_from_move(move_pct)

            prefix = f"outcome_{horizon}m"
            updates[f"{prefix}_spot"] = target_spot
            updates[f"{prefix}_pct"] = round(move_pct, 6)
            updates[f"{prefix}_direction"] = actual_direction
            updates[f"hit_{horizon}m"] = _hit(
                str(direction).upper(),
                actual_direction,
            )
            updates[f"resolved_{horizon}m"] = 1

        if not updates:
            continue

        assignments = ", ".join(
            f"{column} = ?" for column in updates
        )
        values = list(updates.values()) + [
            symbol,
            observation_minute,
        ]

        conn.execute(
            f"""
            UPDATE short_horizon_observations
            SET {assignments}
            WHERE symbol = ?
              AND observation_minute = ?
            """,
            values,
        )


def _prune(conn: sqlite3.Connection, db_path: str) -> None:
    now = datetime.now(timezone.utc)
    last_pruned = _last_pruned_at.get(db_path)

    if (
        last_pruned is not None
        and (now - last_pruned).total_seconds()
        < PRUNE_INTERVAL_SECONDS
    ):
        return

    cutoff = (
        now - timedelta(days=RETENTION_DAYS)
    ).isoformat(timespec="seconds")

    conn.execute(
        """
        DELETE FROM short_horizon_observations
        WHERE observation_time < ?
        """,
        (cutoff,),
    )

    _last_pruned_at[db_path] = now


def record_short_horizon_observation(
    engine_result,
    decision_dict: dict,
    db_path: str = DB_PATH,
) -> None:
    """Record one prediction and resolve any now-available outcomes.

    Never raises. A validation-history failure must not affect the live
    decision pipeline.
    """
    if engine_result is None or not decision_dict:
        return

    try:
        short_horizon = decision_dict.get("shortHorizon")
        if not isinstance(short_horizon, dict):
            return

        symbol = str(
            getattr(engine_result, "symbol", "")
            or ""
        ).strip()
        if not symbol:
            return

        observation_time = str(
            decision_dict.get("decisionTimestamp")
            or getattr(engine_result, "decision_timestamp", "")
            or ""
        ).strip()

        observation_minute = _minute(observation_time)
        if observation_minute is None:
            return

        spot = float(
            getattr(engine_result, "spot", 0.0)
            or 0.0
        )
        if not isfinite(spot) or spot <= 0:
            return

        price = short_horizon.get("price") or {}
        oi = short_horizon.get("oi") or {}
        futures = short_horizon.get("futures") or {}
        compression = short_horizon.get("compression") or {}

        row = (
            observation_time,
            observation_minute,
            symbol,
            spot,
            str(short_horizon.get("direction", "NEUTRAL")),
            str(short_horizon.get("state", "NEUTRAL")),
            float(short_horizon.get("score", 0.0) or 0.0),
            int(short_horizon.get("probability", 0) or 0),
            float(price.get("velocityPctPerMin", 0.0) or 0.0),
            float(price.get("accelerationPctPerMin2", 0.0) or 0.0),
            float(oi.get("score", 0.0) or 0.0),
            float(futures.get("score", 0.0) or 0.0),
            int(bool(compression.get("active"))),
            json.dumps(short_horizon, default=str),
        )

        _ensure_schema(db_path)

        with _lock, sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO short_horizon_observations (
                    observation_time,
                    observation_minute,
                    symbol,
                    spot,
                    direction,
                    state,
                    score,
                    move_likelihood,
                    velocity,
                    acceleration,
                    oi_score,
                    futures_score,
                    compression_active,
                    prediction_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )

            # Resolve older observations from the same symbol whenever
            # their exact +1/+2/+3 minute closes have arrived.
            _resolve_pending(conn, symbol)

            _prune(conn, db_path)

    except Exception as exc:
        logger.warning(
            "[short_horizon_recorder] could not record observation: %s",
            exc,
        )


def load_short_horizon_observations(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Load chronological short-horizon validation observations."""
    _ensure_schema(db_path)

    query = """
        SELECT *
        FROM short_horizon_observations
        WHERE symbol = ?
    """
    params: list[object] = [symbol]

    if start:
        query += " AND observation_time >= ?"
        params.append(start)

    if end:
        query += " AND observation_time < ?"
        params.append(end)

    query += " ORDER BY observation_minute ASC"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    return [dict(row) for row in rows]
