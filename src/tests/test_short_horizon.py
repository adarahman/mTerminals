from __future__ import annotations

from analytics import oversold_oi_support as history
from decision.short_horizon import detect_short_horizon


def _seed(symbol: str, prices: list[float]) -> None:
    history.reset_spot_rsi_history()

    for minute, price in enumerate(prices):
        history._minute_closes[symbol].append((minute * 60, price))


def test_insufficient_history_is_neutral():
    symbol = "TEST"

    _seed(symbol, [100.0, 100.05, 100.10])

    result = detect_short_horizon(symbol)

    assert result["state"] == "NEUTRAL"
    assert result["direction"] == "NEUTRAL"
    assert result["probability"] == 0
    assert "Insufficient 1-minute history" in result["evidence"]


def test_accelerating_up_move_builds_upward_pressure():
    symbol = "TEST"

    _seed(
        symbol,
        [
            100.00,
            100.02,
            100.05,
            100.12,
            100.22,
        ],
    )

    result = detect_short_horizon(symbol)

    assert result["direction"] == "UP"
    assert result["score"] > 0
    assert result["price"]["accelerationPctPerMin2"] > 0
    assert result["state"] in {
        "PRESSURE_BUILDING",
        "BREAKOUT_TRIGGER",
        "MOVE",
    }


def test_accelerating_down_move_builds_downward_pressure():
    symbol = "TEST"

    _seed(
        symbol,
        [
            100.00,
            99.98,
            99.95,
            99.88,
            99.78,
        ],
    )

    result = detect_short_horizon(symbol)

    assert result["direction"] == "DOWN"
    assert result["score"] < 0
    assert result["price"]["accelerationPctPerMin2"] < 0
    assert result["state"] in {
        "PRESSURE_BUILDING",
        "BREAKOUT_TRIGGER",
        "MOVE",
    }


def test_oi_confirmation_is_exposed():
    symbol = "TEST"

    _seed(
        symbol,
        [
            100.00,
            100.02,
            100.05,
            100.12,
        ],
    )

    result = detect_short_horizon(
        symbol,
        oi_score=0.60,
    )

    assert result["oi"]["direction"] == "UP"
    assert result["oi"]["confirmed"] is True
    assert result["oi"]["score"] == 0.60
    assert result["direction"] == "UP"


def test_conflicting_oi_does_not_create_a_second_decision_engine():
    symbol = "TEST"

    _seed(
        symbol,
        [
            100.00,
            100.02,
            100.05,
            100.12,
        ],
    )

    result = detect_short_horizon(
        symbol,
        oi_score=-0.60,
    )

    assert result["oi"]["direction"] == "DOWN"
    assert result["oi"]["confirmed"] is True
    assert result["score"] < 1.0
    assert result["direction"] in {"UP", "NEUTRAL"}


def test_compression_then_expansion_is_detected():
    symbol = "TEST"

    _seed(
        symbol,
        [
            100.00,
            100.01,
            100.02,
            100.01,
            100.30,
            100.50,
            100.65,
        ],
    )

    result = detect_short_horizon(symbol)

    assert result["compression"]["previousRange3mPct"] > 0
    assert result["compression"]["expanding"] is True


def test_futures_confirmation_is_exposed():
    symbol = "TEST"

    _seed(
        symbol,
        [
            100.00,
            100.02,
            100.05,
            100.12,
        ],
    )

    result = detect_short_horizon(
        symbol,
        fut_score=0.50,
    )

    assert result["futures"]["direction"] == "UP"
    assert result["futures"]["confirmed"] is True


def test_probability_is_bounded_evidence_score():
    symbol = "TEST"

    _seed(
        symbol,
        [
            100.00,
            100.20,
            100.45,
            100.80,
        ],
    )

    result = detect_short_horizon(
        symbol,
        oi_score=1.0,
        fut_score=1.0,
    )

    assert 0 <= result["probability"] <= 95

def test_large_acceleration_with_negligible_velocity_does_not_create_direction():
    symbol = "TEST"

    _seed(
        symbol,
        [
            100.00,
            100.50,
            100.00,
            100.00,
        ],
    )

    result = detect_short_horizon(symbol)

    assert abs(result["price"]["velocityPctPerMin"]) < 0.03
    assert abs(result["price"]["accelerationPctPerMin2"]) > 0.03
    assert result["direction"] == "NEUTRAL"
