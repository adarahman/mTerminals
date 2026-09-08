from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from analytics.oversold_oi_support import (
    reset_spot_rsi_history,
    update_spot_rsi,
)
from backtest.short_horizon_recorder import (
    load_short_horizon_observations,
    record_short_horizon_observation,
)


def _decision(timestamp):
    return {
        "decisionTimestamp": timestamp,
        "shortHorizon": {
            "horizon": "2-3m",
            "state": "PRESSURE_BUILDING",
            "direction": "UP",
            "probability": 55,
            "probabilityCalibrated": False,
            "score": 0.55,
            "price": {
                "velocityPctPerMin": 0.10,
                "accelerationPctPerMin2": 0.02,
            },
            "oi": {"score": 0.3},
            "futures": {"score": 0.2},
            "compression": {"active": True},
        },
    }


def _engine(spot=24000):
    return SimpleNamespace(
        symbol="NIFTY",
        spot=spot,
    )


def test_records_one_observation_per_symbol_minute(tmp_path):
    reset_spot_rsi_history()

    start = datetime(
        2026, 9, 8, 9, 15, tzinfo=timezone.utc
    )

    update_spot_rsi("NIFTY", 24000, start.isoformat())

    db = str(tmp_path / "decision.db")

    record_short_horizon_observation(
        _engine(),
        _decision(start.isoformat()),
        db,
    )

    record_short_horizon_observation(
        _engine(),
        _decision(
            (start + timedelta(seconds=30)).isoformat()
        ),
        db,
    )

    rows = load_short_horizon_observations(
        "NIFTY",
        db_path=db,
    )

    assert len(rows) == 1
    assert rows[0]["spot"] == 24000


def test_resolves_exact_one_two_three_minute_outcomes(tmp_path):
    reset_spot_rsi_history()

    start = datetime(
        2026, 9, 8, 9, 15, tzinfo=timezone.utc
    )

    prices = [24000, 24050, 23950, 24100]

    for offset, price in enumerate(prices):
        update_spot_rsi(
            "NIFTY",
            price,
            (start + timedelta(minutes=offset)).isoformat(),
        )

    db = str(tmp_path / "decision.db")

    record_short_horizon_observation(
        _engine(24000),
        _decision(start.isoformat()),
        db,
    )

    row = load_short_horizon_observations(
        "NIFTY",
        db_path=db,
    )[0]

    assert row["outcome_1m_spot"] == 24050
    assert row["outcome_2m_spot"] == 23950
    assert row["outcome_3m_spot"] == 24100

    assert row["outcome_1m_direction"] == "UP"
    assert row["outcome_2m_direction"] == "DOWN"
    assert row["outcome_3m_direction"] == "UP"

    assert row["hit_1m"] == 1
    assert row["hit_2m"] == 0
    assert row["hit_3m"] == 1

    assert row["resolved_1m"] == 1
    assert row["resolved_2m"] == 1
    assert row["resolved_3m"] == 1


def test_missing_exact_minute_remains_pending(tmp_path):
    reset_spot_rsi_history()

    start = datetime(
        2026, 9, 8, 9, 15, tzinfo=timezone.utc
    )

    update_spot_rsi("NIFTY", 24000, start.isoformat())

    update_spot_rsi(
        "NIFTY",
        24050,
        (start + timedelta(minutes=1)).isoformat(),
    )

    update_spot_rsi(
        "NIFTY",
        24100,
        (start + timedelta(minutes=3)).isoformat(),
    )

    db = str(tmp_path / "decision.db")

    record_short_horizon_observation(
        _engine(24000),
        _decision(start.isoformat()),
        db,
    )

    row = load_short_horizon_observations(
        "NIFTY",
        db_path=db,
    )[0]

    assert row["resolved_1m"] == 1
    assert row["resolved_2m"] == 0
    assert row["resolved_3m"] == 1
    assert row["outcome_2m_spot"] is None


def test_pending_observation_resolves_on_later_tick(tmp_path):
    reset_spot_rsi_history()

    start = datetime(
        2026, 9, 8, 9, 15, tzinfo=timezone.utc
    )

    update_spot_rsi(
        "NIFTY",
        24000,
        start.isoformat(),
    )

    update_spot_rsi(
        "NIFTY",
        24050,
        (start + timedelta(minutes=1)).isoformat(),
    )

    db = str(tmp_path / "decision.db")

    record_short_horizon_observation(
        _engine(24000),
        _decision(start.isoformat()),
        db,
    )

    update_spot_rsi(
        "NIFTY",
        24100,
        (start + timedelta(minutes=2)).isoformat(),
    )

    record_short_horizon_observation(
        _engine(24100),
        _decision(
            (start + timedelta(minutes=2)).isoformat()
        ),
        db,
    )

    row = load_short_horizon_observations(
        "NIFTY",
        db_path=db,
    )[0]

    assert row["resolved_2m"] == 1
    assert row["outcome_2m_spot"] == 24100
