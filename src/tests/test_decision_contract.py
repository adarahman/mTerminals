from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from analytics.oversold_oi_support import reset_spot_rsi_history, update_spot_rsi
from decision.confidence import compute_confidence
from decision.decision_engine import DecisionEngine
from decision.types import ActiveSignal, DecisionResult


def _engine_result(**overrides):
    values = dict(
        spot=24000, atm=24000, strike_step=50, lot_size=25, dte=3,
        expiry="2026-08-13", total_pcr=1.25, oi_chg_pcr=1.1,
        max_pain=24000, max_pain_dist=0, ce_wall=24200, pe_wall=23800,
        india_vix=15, base_iv=14, iv_rank=45, basis=12,
        bias="Bullish", fut_signal="Long Buildup", ce_premium=120,
        pe_premium=110, atm_theta=-8, vel_df=None, vol_oi_ratios={},
        smart_money_top=None, trade_grade="A", trap_warn="None",
        wing_premiums=None, fut_oi=1000,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_decision_contract_has_provenance_and_visible_evidence():
    result = DecisionEngine().evaluate(_engine_result(), {
        "_decision_timestamp": "2026-08-08T10:00:00+05:30",
        "_state_version": "NIFTY:2026-08-13:2026-08-08T10:00:00+05:30",
    }).to_dict()

    assert result["decisionTimestamp"] == "2026-08-08T10:00:00+05:30"
    assert result["stateVersion"].startswith("NIFTY:")
    assert result["evidenceCoverage"] == 85
    assert result["degraded"] is False
    assert result["missingInputs"] == ["oi_velocity"]
    assert len(result["contributors"]) == 6
    assert result["contributors"][-1]["key"] == "oversold_oi_support"
    assert result["contributors"][-1]["state"] == "unavailable"
    assert result["tradeGrade"] == "A"
    assert result["importantLevels"]["atm"] == 24000


def test_missing_required_input_degrades_and_disables_execution():
    result = DecisionEngine().evaluate(_engine_result(total_pcr=0), {}).to_dict()
    assert result["degraded"] is True
    assert "pcr" in result["missingInputs"]
    assert result["actionType"] == "WAIT"
    assert result["suggestedStrike"] is None
    assert result["executeRecommended"] is False
    assert "Required decision evidence" in result["strategyCaution"]


def test_missing_futures_quote_is_not_scored_as_bearish_evidence():
    result = DecisionEngine().evaluate(
        _engine_result(fut_signal="Unknown", basis=0, fut_oi=0), {}
    ).to_dict()

    futures = next(
        contributor
        for contributor in result["contributors"]
        if contributor["key"] == "futures"
    )
    assert futures["available"] is False
    assert futures["score"] is None
    assert "futures" in result["missingInputs"]
    assert result["degraded"] is True
    assert result["bias"] == "BULLISH"


def test_flat_futures_oi_is_neutral_but_not_missing():
    result = DecisionEngine().evaluate(
        _engine_result(fut_signal="", fut_oi=1000), {}
    ).to_dict()
    futures = next(c for c in result["contributors"] if c["key"] == "futures")
    assert futures["available"] is True
    assert futures["score"] == 0.0
    assert "futures" not in result["missingInputs"]
    assert result["degraded"] is False


def test_directional_setup_fails_closed_below_execution_confidence():
    # Expiry-day decay can leave the weighted direction intact while reducing
    # confidence below the execution boundary. The headline must not continue
    # to recommend a directional order in that state.
    result = DecisionEngine().evaluate(_engine_result(dte=0), {}).to_dict()
    assert result["bias"] == "BULLISH"
    assert result["confidence"] < 40
    assert result["actionType"] == "WAIT"
    assert result["suggestedStrike"] is None
    assert "below execution threshold" in result["action"]
    assert result["executeRecommended"] is False


def test_falling_session_cannot_emit_moderate_bullish_trade():
    result = DecisionEngine().evaluate(
        _engine_result(spot_chg_pct=-0.50), {}
    ).to_dict()
    assert result["biasStrength"] == "WEAK"
    assert result["actionType"] == "WAIT"


def test_near_term_countertrend_move_clamps_bullish_call_even_mid_session():
    # spot_chg_pct=0 here (whole-session move silent, e.g. mid-day chop
    # relative to the open) — the ONLY thing available to catch this
    # countertrend case is the near-term rolling-closes read.
    reset_spot_rsi_history()
    symbol = "NIFTY_TEST_MOMENTUM_DOWN"
    start = datetime(2026, 8, 8, 9, 15, tzinfo=timezone.utc)
    for i, price in enumerate([24100, 24080, 24060, 24040, 24020, 23980]):
        update_spot_rsi(symbol, price, (start + timedelta(minutes=i)).isoformat())

    result = DecisionEngine().evaluate(
        _engine_result(symbol=symbol, spot_chg_pct=0.0,
                        total_pcr=1.5, bias="Strong Bullish",
                        fut_signal="Long Buildup", basis=50), {}
    ).to_dict()

    assert result["_debug"]["spot_move_session_pct"] == 0.0
    assert result["_debug"]["near_term_countertrend_clamp"] is True
    assert result["biasStrength"] == "WEAK"
    assert result["actionType"] == "WAIT"


def test_near_term_move_with_the_call_is_not_clamped():
    # Same magnitude of near-term move, but in the SAME direction as the
    # composite — must not be treated as countertrend evidence.
    reset_spot_rsi_history()
    symbol = "NIFTY_TEST_MOMENTUM_UP"
    start = datetime(2026, 8, 8, 9, 15, tzinfo=timezone.utc)
    for i, price in enumerate([23980, 24020, 24040, 24060, 24080, 24100]):
        update_spot_rsi(symbol, price, (start + timedelta(minutes=i)).isoformat())

    result = DecisionEngine().evaluate(
        _engine_result(symbol=symbol, spot_chg_pct=0.0,
                        total_pcr=1.5, bias="Strong Bullish",
                        fut_signal="Long Buildup", basis=50), {}
    ).to_dict()

    assert result["_debug"]["near_term_countertrend_clamp"] is False


def test_evidence_coverage_reduces_confidence():
    complete = compute_confidence(.6, False, "NORMAL", 4, 0, 4, .7, .3, .3,
                                  evidence_coverage=1.0)
    partial = compute_confidence(.6, False, "NORMAL", 4, 0, 4, .7, .3, .3,
                                 evidence_coverage=.5)
    degraded = compute_confidence(.6, False, "NORMAL", 4, 0, 4, .7, .3, .3,
                                  evidence_coverage=.8, critical_inputs_missing=True)
    assert partial < complete
    assert degraded <= 35


def test_active_signals_have_identity_timestamp_priority_and_are_deduplicated():
    decision = DecisionResult(decision_timestamp="2026-08-08T10:00:00+05:30")
    decision.active_signals.extend([
        ActiveSignal("First rendering", "info", 20, "wall:ce"),
        ActiveSignal("Higher-severity rendering", "warn", 5, "wall:ce"),
        ActiveSignal("Different strike", "ok", 10, "oi-velocity:pe:24000:writing"),
    ])

    signals = decision.to_dict()["activeSignals"]

    assert len(signals) == 2
    assert signals[0] == {
        "id": "wall:ce",
        "text": "Higher-severity rendering",
        "severity": "warn",
        "priority": 5,
        "observedAt": "2026-08-08T10:00:00+05:30",
    }
    assert signals[1]["id"] == "oi-velocity:pe:24000:writing"
