from types import SimpleNamespace

from decision.confidence import compute_confidence
from decision.decision_engine import DecisionEngine


def _engine_result(**overrides):
    values = dict(
        spot=24000, atm=24000, strike_step=50, lot_size=25, dte=3,
        expiry="2026-08-13", total_pcr=1.25, oi_chg_pcr=1.1,
        max_pain=24000, max_pain_dist=0, ce_wall=24200, pe_wall=23800,
        india_vix=15, base_iv=14, iv_rank=45, basis=12,
        bias="Bullish", fut_signal="Long Buildup", ce_premium=120,
        pe_premium=110, atm_theta=-8, vel_df=None, vol_oi_ratios={},
        smart_money_top=None, trade_grade="A", trap_warn="None",
        wing_premiums=None,
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
    assert result["evidenceCoverage"] == 82
    assert result["degraded"] is False
    assert result["missingInputs"] == ["oi_velocity", "smart_money"]
    assert len(result["contributors"]) == 6
    assert result["tradeGrade"] == "A"
    assert result["importantLevels"]["atm"] == 24000


def test_missing_required_input_degrades_and_disables_execution():
    result = DecisionEngine().evaluate(_engine_result(total_pcr=0), {}).to_dict()
    assert result["degraded"] is True
    assert "pcr" in result["missingInputs"]
    assert result["executeRecommended"] is False
    assert "Required decision evidence" in result["strategyCaution"]


def test_evidence_coverage_reduces_confidence():
    complete = compute_confidence(.6, False, "NORMAL", 4, 0, 4, .7, .3, .3,
                                  evidence_coverage=1.0)
    partial = compute_confidence(.6, False, "NORMAL", 4, 0, 4, .7, .3, .3,
                                 evidence_coverage=.5)
    degraded = compute_confidence(.6, False, "NORMAL", 4, 0, 4, .7, .3, .3,
                                  evidence_coverage=.8, critical_inputs_missing=True)
    assert partial < complete
    assert degraded <= 35
