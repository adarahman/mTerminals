"""Unit tests for decision/signal_builder.py's sub-signal scorers.

These lock in the PCR nomenclature the module docstring calls out as a
previously-fixed bug (LOW PCR = bearish, HIGH PCR = bullish) — a
regression here would be exactly the kind of silent flip that's easy to
reintroduce without a test.
"""

import pandas as pd

from decision.signal_builder import (
    score_pcr, score_engine_bias, score_futures, score_oi_velocity, detect_traps,
)
from decision.types import T, DecisionResult
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
    # Matches oi_analysis.get_oi_velocity()'s REAL output schema (window/
    # strike/ceNow/ceDOI/peNow/peDOI/...) — the fixture previously used
    # Strike/CE_OI_Delta/PE_OI_Delta/CE_OI/PE_OI, which is what the old
    # (buggy) detect_traps() code read, not what get_oi_velocity() ever
    # actually produces. That let this test pass without ever exercising
    # the real column-mismatch bug fixed in signal_builder.py.
    return pd.DataFrame([
        {"window": 5, "strike": pe_wall, "ceDOI": 0, "peDOI": 1_000_000,
         "ceNow": 1, "peNow": 1_000_000},
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


# ── score_oi_velocity() ──────────────────────────────────────────────────
# No coverage existed for this function before — it was silently returning
# 0.0 for every real vel_df (see signal_builder.py's fix docstring), so
# there was nothing here to catch it. These pin the real
# get_oi_velocity()-shaped schema (window/strike/ceNow/ceDOI/peNow/peDOI)
# so a future schema drift breaks a test instead of silently zeroing out
# the sub-score again.

def _vel_df(window=5, rows=None):
    return pd.DataFrame(rows or [])


def test_oi_velocity_zero_on_empty_or_none():
    out = DecisionResult()
    assert score_oi_velocity(None, spot=24100, step=50, out=out) == 0.0
    assert score_oi_velocity(_vel_df(), spot=24100, step=50, out=out) == 0.0


def test_ce_writing_is_bearish():
    vel_df = _vel_df(rows=[
        {"window": 5, "strike": 24150, "ceNow": 1_000_000, "ceDOI": 200_000,
         "peNow": 500_000, "peDOI": 0},
    ])
    out = DecisionResult()
    score = score_oi_velocity(vel_df, spot=24100, step=50, out=out)
    assert score < 0


def test_pe_writing_is_bullish():
    vel_df = _vel_df(rows=[
        {"window": 5, "strike": 24050, "ceNow": 500_000, "ceDOI": 0,
         "peNow": 1_000_000, "peDOI": 200_000},
    ])
    out = DecisionResult()
    score = score_oi_velocity(vel_df, spot=24100, step=50, out=out)
    assert score > 0


def test_window_filter_selects_only_matching_rows():
    # Same strike, opposite signals at the 5-min vs 15-min window — proves
    # the `window` param actually filters rather than averaging everything
    # in the DataFrame together (which is what happened before the fix,
    # since no window filtering existed at all).
    vel_df = _vel_df(rows=[
        {"window": 5,  "strike": 24150, "ceNow": 1_000_000, "ceDOI": 200_000,
         "peNow": 500_000, "peDOI": 0},
        {"window": 15, "strike": 24150, "ceNow": 1_000_000, "ceDOI": -200_000,
         "peNow": 500_000, "peDOI": 0},
    ])
    out = DecisionResult()
    score_5min  = score_oi_velocity(vel_df, spot=24100, step=50, out=out, window=5)
    score_15min = score_oi_velocity(vel_df, spot=24100, step=50, out=out, window=15)
    assert score_5min < 0
    assert score_15min > 0


def test_unknown_window_returns_zero_not_all_rows():
    vel_df = _vel_df(rows=[
        {"window": 5, "strike": 24150, "ceNow": 1_000_000, "ceDOI": 200_000,
         "peNow": 500_000, "peDOI": 0},
    ])
    out = DecisionResult()
    assert score_oi_velocity(vel_df, spot=24100, step=50, out=out, window=30) == 0.0


def test_falling_price_with_rising_futures_oi_is_short_buildup():
    from analytics.market_regime import classify_market_regime

    result = classify_market_regime(-0.47, 0.60, has_oi_data=True)
    assert result["regime"] == "Short Build-up"


def test_falling_price_with_falling_futures_oi_is_long_unwinding():
    from analytics.market_regime import classify_market_regime

    result = classify_market_regime(-0.47, -0.60, has_oi_data=True)
    assert result["regime"] == "Long Unwinding"


def test_basis_cannot_create_futures_direction_without_regime():
    assert score_futures("", basis=200) == 0.0
    assert score_futures("Unknown", basis=-200) == 0.0


def test_spread_credit_deducts_hedge_premium():
    from decision.strategy_selection import suggest_strategy

    name, strategy = suggest_strategy(
        "BULLISH", "MODERATE", 24050, 50,
        ce_ltp=70, pe_ltp=44.85, lot_size=65,
        expiry="01-Sep-2026", dte=1, vix_tag="LOW", iv_rank=35,
        wing_ltp={"pe_buy": 22.35, "ce_buy": 10},
    )
    assert name == "Bull Put Spread"
    assert strategy["netPremium"] == 22.5
    assert strategy["maxProfit"] == 1462.5
    assert strategy["maxLoss"] == 1787.5
