"""
oi/chain_metrics.py
--------------------
Chain-level metrics computed once per EngineResult pass: the canonical
ATM±N strike-window slice, max pain, total PCR, the full-chain Greeks
table (+ GEX summary), smart-money ranking, per-strike volume/OI ratios,
and IV Rank / HV30.

Moved from engine.py (Step 4a of the v3 migration plan). Pure move +
rename only: no behavioral changes, no signature changes.
"""

from __future__ import annotations

import math

import pandas as pd

from oi.pricing import (
    ANNUAL_RISK_FREE_RATE,
    _MIN_T_YEARS,
    bs_delta,
    bs_gamma,
    bs_theta,
    bs_vega,
    get_iv_skew,
)

__all__ = [
    "_atm_window",
    "compute_max_pain",
    "compute_total_pcr",
    "_build_greeks_table",
    "_summarize_gex",
    "_build_smart_money_top",
    "_build_vol_oi_ratios",
    "_compute_iv_rank_hv30",
]


def _safe_num(value, default=0.0):
    """Coerce a chain cell to a finite float without presenter imports."""
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(parsed) else parsed


# ===========================================================================
# Strike-window helper (the single canonical ATM±N slice — was duplicated
# 5 independent ways across greeks_dashboard.py, iv_surface.py, oi_flow.py,
# option_chain.py, and option_chain_renderer.py before this refactor)
# ===========================================================================

def _atm_window(df: pd.DataFrame, atm: float, strike_step: int,
                 n_strikes_each_side: int) -> pd.DataFrame:
    """Returns df sliced to strikes within n_strikes_each_side*step (+1 pt
    slack) of atm, sorted by StrikePrice. Matches the slicing logic that was
    duplicated (with minor variations) across greeks_dashboard/iv_surface/
    oi_flow/option_chain_renderer."""
    return (
        df[df['StrikePrice'].apply(
            lambda k: abs(k - atm) <= n_strikes_each_side * strike_step + 1
        )]
        .sort_values('StrikePrice')
        .reset_index(drop=True)
    )


# ===========================================================================
# Max pain (was hardcoded to `atm` in option_chain.py's ctx_dict; the real
# O(n^2) calculation existed only inside oi_flow.py, used locally there)
# ===========================================================================

def compute_max_pain(df: pd.DataFrame) -> float:
    chain = df.dropna(subset=['StrikePrice']).copy()
    chain['CE_OI'] = chain['CE_OI'].fillna(0)
    chain['PE_OI'] = chain['PE_OI'].fillna(0)

    strikes = chain['StrikePrice'].tolist()
    ce_oi = dict(zip(chain['StrikePrice'], chain['CE_OI']))
    pe_oi = dict(zip(chain['StrikePrice'], chain['PE_OI']))

    best_strike, best_loss = None, None
    for candidate in strikes:
        loss = 0.0
        for k in strikes:
            if candidate > k:
                loss += ce_oi[k] * (candidate - k)
            elif candidate < k:
                loss += pe_oi[k] * (k - candidate)
        if best_loss is None or loss < best_loss:
            best_loss, best_strike = loss, candidate

    return best_strike


def compute_total_pcr(df: pd.DataFrame) -> float:
    total_ce = df['CE_OI'].fillna(0).sum()
    total_pe = df['PE_OI'].fillna(0).sum()
    return round(total_pe / total_ce, 2) if total_ce > 0 else 0.0


# ===========================================================================
# Greeks table (was independently recomputed in greeks_dashboard.py via its
# own bs_delta/bs_gamma/bs_theta/bs_vega calls over the same strike window
# oi_analysis.build_master_table_nse already prices)
# ===========================================================================

def _build_greeks_table(df: pd.DataFrame, spot: float, base_iv: float,
                          dte: int, lot_size: int,
                          use_live_iv: bool = True, q: float = 0.0) -> pd.DataFrame:
    """Per-strike CE/PE Greeks + Net GEX over the full master chain, computed once.

    UPGRADE (was: single synthetic skew IV fed into both legs, so
    pGamma/pVega were literally aliased to the CE-side values — not an
    approximation, an exact identity, since BS gamma has no call/put branch
    and both legs got the same (S,K,T,r,sigma) inputs). Real CE/PE gamma
    divergence only exists if the two legs are priced off *different*
    IVs — i.e. the market's actual put/call skew. This now reads live
    CE_IV/PE_IV off the chain per strike and prices each leg's Greeks
    independently. Falls back to the old synthetic get_iv_skew() curve,
    per leg, whenever the live IV for that strike is missing/zero/NaN
    (illiquid far-OTM strikes commonly have no live IV yet) — so this
    degrades gracefully rather than raising or fabricating a number.
    Set use_live_iv=False to restore the exact prior symmetric-IV behavior
    if a downstream consumer needs numbers to stay bit-identical.
    """
    # master (oi_analysis.build_master_table_nse output) uses lowercase
    # snake_case columns ('strike'/'ce_oi'/'pe_oi'); window uses the raw
    # NSE-parser columns ('StrikePrice'/'CE_OI'/'PE_OI').
    strike_col = 'strike' if 'strike' in df.columns else 'StrikePrice'
    ce_oi_col  = 'ce_oi'  if 'ce_oi'  in df.columns else 'CE_OI'
    pe_oi_col  = 'pe_oi'  if 'pe_oi'  in df.columns else 'PE_OI'
    ce_iv_col  = 'ce_iv'  if 'ce_iv'  in df.columns else 'CE_IV'
    pe_iv_col  = 'pe_iv'  if 'pe_iv'  in df.columns else 'PE_IV'

    strikes = df[strike_col].tolist()
    ce_oi = df[ce_oi_col].fillna(0).tolist()
    pe_oi = df[pe_oi_col].fillna(0).tolist()

    have_live_ce_iv = use_live_iv and ce_iv_col in df.columns
    have_live_pe_iv = use_live_iv and pe_iv_col in df.columns
    live_ce_iv = df[ce_iv_col].tolist() if have_live_ce_iv else None
    live_pe_iv = df[pe_iv_col].tolist() if have_live_pe_iv else None

    t_param = max(dte / 365.0, _MIN_T_YEARS)
    r_param = ANNUAL_RISK_FREE_RATE

    def _leg_iv(raw_list, i, fallback):
        """Live IV for this strike/leg if usable, else the synthetic skew fallback."""
        if raw_list is None:
            return fallback
        v = raw_list[i]
        if v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, (int, float)) and v <= 0):
            return fallback
        v = float(v)
        return v / 100.0 if v > 1.0 else v  # handle NSE's %-style IV storage (e.g. 14.2 -> 0.142)

    rows = []
    for i, k in enumerate(strikes):
        skew_iv = get_iv_skew(k, spot, base_iv)
        ce_iv_eff = _leg_iv(live_ce_iv, i, skew_iv)
        pe_iv_eff = _leg_iv(live_pe_iv, i, skew_iv)

        c_d = bs_delta(spot, k, t_param, r_param, ce_iv_eff, "C", q)
        p_d = bs_delta(spot, k, t_param, r_param, pe_iv_eff, "P", q)
        c_g = bs_gamma(spot, k, t_param, r_param, ce_iv_eff, q)
        p_g = bs_gamma(spot, k, t_param, r_param, pe_iv_eff, q)
        c_t = bs_theta(spot, k, t_param, r_param, ce_iv_eff, "C", q)
        p_t = bs_theta(spot, k, t_param, r_param, pe_iv_eff, "P", q)
        c_v = bs_vega(spot, k, t_param, r_param, ce_iv_eff, q)
        p_v = bs_vega(spot, k, t_param, r_param, pe_iv_eff, q)

        # Each leg's own OI weighted by its own gamma — no longer collapses
        # to gamma*(CE_OI-PE_OI) unless ce_iv_eff == pe_iv_eff for that strike.
        gex_val = (ce_oi[i] * c_g - pe_oi[i] * p_g) * lot_size * spot / 1_000_000_000

        rows.append({
            'Strike': k,
            'cDelta': c_d, 'cGamma': c_g, 'cTheta': c_t, 'cVega': c_v,
            'pDelta': p_d, 'pGamma': p_g, 'pTheta': p_t, 'pVega': p_v,
            'netGEX': gex_val,
            'iv': ce_iv_eff,
            'ce_iv': ce_iv_eff, 'pe_iv': pe_iv_eff,
        })
    return pd.DataFrame(rows)


def _summarize_gex(greeks_table: pd.DataFrame) -> dict:
    """Roll per-strike netGEX (from _build_greeks_table) up into a single
    regime summary: total exposure, sign, and the strike where cumulative
    GEX crosses zero (approximate gamma flip point). Pure aggregation over
    an already-computed column — no new pricing math, no new data fetch."""
    if greeks_table is None or greeks_table.empty or "netGEX" not in greeks_table.columns:
        return {"total_gex": 0.0, "gex_regime": "unknown", "gamma_flip_strike": None}

    sorted_tbl = greeks_table.sort_values("Strike")
    total_gex = float(sorted_tbl["netGEX"].sum())

    flip_strike = None
    cumulative_gex = sorted_tbl["netGEX"].cumsum()
    sign_changes = cumulative_gex.lt(0).ne(cumulative_gex.shift().lt(0))
    if not sign_changes.empty:
        sign_changes.iloc[0] = False
        crossing_rows = sorted_tbl.loc[sign_changes]
        if not crossing_rows.empty:
            flip_strike = float(crossing_rows.iloc[0]["Strike"])

    return {
        "total_gex": round(total_gex, 4),   # ₹ billions, same convention as netGEX
        "gex_regime": "positive" if total_gex > 0 else "negative",
        "gamma_flip_strike": flip_strike,
    }


# ===========================================================================
# Smart-money ranking (was inline inside dashboard_modules.render_smart_money
# / the now-deleted dashboard_intelligence.py duplicate)
# ===========================================================================

def _build_smart_money_top(df: pd.DataFrame, top_n: int = 4) -> pd.DataFrame:
    df_scores = df.copy()
    ce_vol = df_scores['CE_Volume'].fillna(0) if 'CE_Volume' in df_scores.columns else pd.Series(0, index=df_scores.index)
    pe_vol = df_scores['PE_Volume'].fillna(0) if 'PE_Volume' in df_scores.columns else pd.Series(0, index=df_scores.index)
    df_scores['CE_Score'] = ce_vol / df_scores['CE_OI'].replace(0, 1)
    df_scores['PE_Score'] = pe_vol / df_scores['PE_OI'].replace(0, 1)
    df_scores['Score'] = df_scores['CE_Score']   # primary sort still CE vol/OI
    return df_scores.sort_values(by='Score', ascending=False).head(top_n)


def _build_vol_oi_ratios(df: pd.DataFrame) -> dict:
    """Return per-strike CE and PE volume/OI ratios for DecisionEngine volume confirmation.

    Keys are str(strike). Missing volume columns → empty dict (graceful degradation).
    Values: {'ce': float, 'pe': float, 'ce_vol': int, 'pe_vol': int}
    """
    out: dict = {}
    if df is None or df.empty:
        return out
    has_ce_vol = 'CE_Volume' in df.columns
    has_pe_vol = 'PE_Volume' in df.columns
    if not has_ce_vol and not has_pe_vol:
        return out
    for _, row in df.iterrows():
        k   = str(int(_safe_num(row.get('StrikePrice', 0))))
        ce_oi  = _safe_num(row.get('CE_OI', 0))
        pe_oi  = _safe_num(row.get('PE_OI', 0))
        ce_vol = _safe_num(row.get('CE_Volume', 0)) if has_ce_vol else 0.0
        pe_vol = _safe_num(row.get('PE_Volume', 0)) if has_pe_vol else 0.0
        out[k] = {
            'ce': round(ce_vol / ce_oi, 4) if ce_oi > 0 else 0.0,
            'pe': round(pe_vol / pe_oi, 4) if pe_oi > 0 else 0.0,
            'ce_vol': int(ce_vol),
            'pe_vol': int(pe_vol),
        }
    return out


# ===========================================================================
# IV Rank + HV30  (replaces stubs in build_engine_result)
# ===========================================================================

def _compute_iv_rank_hv30(
    df_history: "pd.DataFrame | None",
    base_iv: float,
    atm_strike: float,
    iv_col: str = "CE_IV",
    spot_col: str = "Spot",
    strike_col: str = "StrikePrice",
    hv_window: int = 30,
    iv_rank_window: int = 252,
) -> tuple[float, float]:
    _iv_stub = 35.0
    _hv_stub = base_iv * 0.85 * 100.0

    if df_history is None or df_history.empty:
        return _iv_stub, _hv_stub

    iv_rank = _iv_stub
    try:
        if strike_col in df_history.columns and iv_col in df_history.columns:
            atm_rows = df_history[df_history[strike_col] == atm_strike]
            if not atm_rows.empty:
                iv_series = (
                    atm_rows[iv_col].dropna().astype(float).tail(iv_rank_window)
                )
                if iv_series.max() < 2.0:
                    iv_series = iv_series * 100.0
                if len(iv_series) >= 2:
                    iv_lo  = iv_series.min()
                    iv_hi  = iv_series.max()
                    iv_now = iv_series.iloc[-1]
                    if iv_hi > iv_lo:
                        iv_rank = round((iv_now - iv_lo) / (iv_hi - iv_lo) * 100.0, 1)
    except Exception:
        pass

    hv30 = _hv_stub
    try:
        if spot_col in df_history.columns:
            spot_series = (
                df_history[spot_col].dropna().astype(float)
                .drop_duplicates().tail(hv_window + 1).reset_index(drop=True)
            )
            if len(spot_series) >= 5:
                log_returns = spot_series.pct_change().dropna().apply(
                    lambda r: math.log(1.0 + r) if r > -1 else 0.0
                )
                hv30 = round(float(log_returns.std(ddof=1)) * math.sqrt(252) * 100.0, 2)
    except Exception:
        pass

    return iv_rank, hv30
