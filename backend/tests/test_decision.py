"""Unit tests for decision/signal_builder.py's sub-signal scorers.

These lock in the PCR nomenclature the module docstring calls out as a
previously-fixed bug (LOW PCR = bearish, HIGH PCR = bullish) — a
regression here would be exactly the kind of silent flip that's easy to
reintroduce without a test.
"""

import pandas as pd

from decision.signal_builder import score_pcr, score_engine_bias, score_futures, detect_traps
from decision.types import T
from oi.pricing import DEFAULT_BASE_IV


def test_high_pcr_is_bullish():
    assert score_pcr(T.PCR_BULL_EXTREME) > 0
    assert score_pcr(T.PCR_BULL) > 0


def test_low_pcr_is_bearish():
    assert score_pcr(T.PCR_BEAR_EXTREME) < 0
    assert score_pcr(T.PCR_BEAR) < 0


def test_pcr_extreme_scores_are_stronger_than_moderate():
    assert score_pcr(T.PCR_BULL_EXTREME) > score_pcr(T.PCR_BULL)
    assert score_pcr(T.PCR_BEAR_EXTREME) < score_pcr(T.PCR_BEAR)


def test_neutral_pcr_band_is_small_and_signed_correctly():
    # Just above 1.0 (mild put-writer dominance) should be mildly bullish,
    # just below 1.0 mildly bearish, and both small relative to the
    # extreme-band scores above.
    assert 0 < score_pcr(1.05) < score_pcr(T.PCR_BULL)
    assert score_pcr(T.PCR_BEAR) < score_pcr(0.95) < 0


def test_engine_bias_direction():
    assert score_engine_bias("Strong Bullish") == 1.0
    assert score_engine_bias("Bullish") > 0
    assert score_engine_bias("Strong Bearish") == -1.0
    assert score_engine_bias("Bearish") < 0
    assert score_engine_bias("Neutral") == 0.0


def test_futures_signal_direction():
    assert score_futures("Long Buildup", basis=0) > 0
    assert score_futures("Short Covering", basis=0) > 0
    assert score_futures("Short Buildup", basis=0) < 0
    assert score_futures("Long Unwinding", basis=0) < 0


def test_futures_basis_nudge_is_bounded():
    # A large positive basis nudges the score up but must not push it
    # past +1.0.
    score = score_futures("Long Buildup", basis=500)
    assert score <= 1.0


# ── detect_traps() ───────────────────────────────────────────────────────
# bear_trap_pcr_min's default used to be a hardcoded 0.80 that happened to
# match T.PCR_BEAR — now it's T.PCR_BEAR directly. These tests pin the
# trap to the canonical threshold itself (not a copy of its value), so a
# future change to T.PCR_BEAR moves the trap boundary with it instead of
# silently diverging again.

def _pe_wall_writing_vel_df(pe_wall):
    return pd.DataFrame([
        {"Strike": pe_wall, "CE_OI_Delta": 0, "PE_OI_Delta": 1_000_000,
         "CE_OI": 1, "PE_OI": 1_000_000},
    ])


def test_bear_trap_fires_below_t_pcr_bear():
    result = detect_traps(
        spot=24170, atm=24100, ce_wall=24500, pe_wall=24090, strike_step=50,
        total_pcr=T.PCR_BEAR - 0.05, base_iv=DEFAULT_BASE_IV, india_vix=15.0,
        vel_df=_pe_wall_writing_vel_df(24090),
    )
    assert result["trap_str"] == "BEAR_TRAP"


def test_bear_trap_does_not_fire_above_t_pcr_bear():
    result = detect_traps(
        spot=24170, atm=24100, ce_wall=24500, pe_wall=24090, strike_step=50,
        total_pcr=T.PCR_BEAR + 0.05, base_iv=DEFAULT_BASE_IV, india_vix=15.0,
        vel_df=_pe_wall_writing_vel_df(24090),
    )
    assert result["trap_str"] != "BEAR_TRAP"


def test_trade_grade_penalized_above_t_vix_normal():
    # Same setup, no walls near spot and no vel_df, so the only variable
    # affecting trade_grade is the VIX penalty — isolates the T.VIX_NORMAL
    # boundary from the trap-count contribution.
    kwargs = dict(spot=24170, atm=24100, ce_wall=24500, pe_wall=23800,
                  strike_step=50, total_pcr=1.0, base_iv=DEFAULT_BASE_IV,
                  vel_df=None)
    below = detect_traps(india_vix=T.VIX_NORMAL - 1, **kwargs)
    above = detect_traps(india_vix=T.VIX_NORMAL + 1, **kwargs)
    assert below["trap_str"] == "BALANCED" and above["trap_str"] == "BALANCED"
    assert below["trade_grade"] == "A"
    assert above["trade_grade"] == "B"


def test_trade_grade_penalized_further_above_24_vix():
    # Was unreachable before the vix_penalty branch order was fixed: any
    # VIX > 24 is also > T.VIX_NORMAL, so the old "> T.VIX_NORMAL" check
    # always won first and the harsher penalty never applied. Now the
    # higher threshold is checked first, so a VIX this high should grade
    # worse than one merely above T.VIX_NORMAL.
    kwargs = dict(spot=24170, atm=24100, ce_wall=24500, pe_wall=23800,
                  strike_step=50, total_pcr=1.0, base_iv=DEFAULT_BASE_IV,
                  vel_df=None)
    moderate = detect_traps(india_vix=T.VIX_NORMAL + 1, **kwargs)
    extreme = detect_traps(india_vix=25.0, **kwargs)
    assert moderate["trade_grade"] == "B"
    assert extreme["trade_grade"] == "C"
