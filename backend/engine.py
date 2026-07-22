"""
engine.py
---------
Single computation pass for the NSE F&O dashboard. Pulls together everything
that was previously computed independently inside multiple render_*.py files
(Greeks, IV skew, ATM±N strike windows, max pain, PCR, strategy pricing,
scenario P&L, smart-money ranking) into ONE EngineResult object.

Render files become pure functions: render_xxx(ws, result: EngineResult, ...)
-> end_row. They read fields off `result` and call ws.range(); they do not
call bs_delta/bs_gamma/get_iv_skew/compute_max_pain/etc. themselves anymore.

Design notes:
- greeks_engine.py has been merged into this file. All Black-Scholes pricing,
  IV skew, IV solver, and OptionChainEngine now live here. greeks_engine.py
  can be deleted; any external code that `from greeks_engine import ...`
  should be updated to `from engine import ...`.
- oi_analysis.py owns everything now, including OI velocity. oi_velocity.py
  has been scrapped — its get_oi_velocity() depended on a df_full_history
  that (via option_chain_json.py) only ever carried a single tick's
  snapshot, so 5/15/30-min lookback could never be satisfied and vel_df
  was structurally always empty. The replacement get_oi_velocity() in
  oi_analysis.py reads directly off the same _HISTORY_MEM parquet-backed
  log that append_json_history() already accumulates tick-by-tick, so a
  real multi-timestamp history is actually available for the lookback.
  mTerminals_json.py's old in-memory fallback (_compute_vel_rows /
  _OI_SNAPSHOTS_MEM) is removed too — redundant now that the primary path
  actually works.
- The two OI-signal classifiers are NOT merged. oi_analysis.classify_buildup
  (vs previous day's close, via NSE's own Change/ChgOI fields) and
  oi_velocity's classify (vs the previous poll/snapshot, via OI_History
  deltas) answer different time-horizon questions and both survive as
  distinctly-named fields: master["ce_signal"]/["pe_signal"] for the daily
  one, result.vel_df["Signal"] for the intraday one.
- Bug fix included here: max_pain was previously hardcoded to `atm` in
  option_chain.py's ctx_dict, even though a correct O(n^2) max-pain
  calculation already existed (oi_flow.compute_max_pain) but was only ever
  used locally inside oi_flow's own render function. engine.py now computes
  it once, correctly, and every section reads the same real value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Optional

import numpy as np
import pandas as pd
from scipy.special import ndtr

from oi_analysis import build_master_table_nse, classify_buildup, signal_strength
from oi_analysis import get_strike_step, get_oi_velocity, append_json_history
from mTerminals_json import fmt_k as _fmt_k


# ===========================================================================
# BLACK-SCHOLES ENGINE  (merged from greeks_engine.py)
# ===========================================================================

__all__ = [
    "bs_call", "bs_put",
    "bs_delta", "bs_gamma", "bs_vega", "bs_theta", "bs_rho",
    "get_iv_skew",
    "norm_pdf", "norm_cdf",
    "solve_iv",
    "get_atm_iv",
    "OptionChainEngine",
    "ANNUAL_RISK_FREE_RATE",
    "DEFAULT_BASE_IV",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ANNUAL_RISK_FREE_RATE = 0.065   # 6.5% — RBI repo rate proxy
DEFAULT_BASE_IV       = 0.15    # 15% fallback when IV solve fails
_IV_MIN               = 0.01
# Floor for the Black-Scholes T (years) parameter. This must only guard
# against literal division-by-zero/sqrt(0) — it must NOT represent "at
# least 1 day of time value". oi_analysis.compute_dte() already returns a
# real intraday fraction on expiry day (minutes-to-close / 1440), matching
# how build_master_table_nse() prices the per-row Greeks. Flooring at
# 1/365 here would silently throw that away and price every expiry-day
# tick as if a full trading day remained — wrong exactly when 0DTE Greeks
# matter most. 1e-6 days (matches oi_analysis.py's own protection floor)
# lets T shrink toward the true value; Black-Scholes handles T→0+ safely
# (d1→±inf, delta→0/1, gamma/vega→0).
_MIN_T_YEARS          = 1e-6 / 365.0
_IV_MAX               = 5.00
_IV_SOLVE_ITERS       = 100
_IV_SOLVE_TOL         = 1e-7
_SQRT_2PI             = math.sqrt(2 * math.pi)


# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------

def norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# d1 / d2 helpers
# ---------------------------------------------------------------------------

def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return _d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


# ---------------------------------------------------------------------------
# Option pricing
# ---------------------------------------------------------------------------

def bs_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes European call price."""
    if T <= 0:
        return max(S - K, 0.0)
    if sigma <= 0 or S <= 0 or K <= 0:
        return max(S - K, 0.0)
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)


def bs_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes European put price."""
    if T <= 0:
        return max(K - S, 0.0)
    if sigma <= 0 or S <= 0 or K <= 0:
        return max(K - S, 0.0)
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


# ---------------------------------------------------------------------------
# Greeks
# ---------------------------------------------------------------------------

def bs_delta(S: float, K: float, T: float, r: float, sigma: float,
             opt_type: str = "C") -> float:
    """Black-Scholes Delta. opt_type: 'C' call / 'P' put."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        if opt_type.upper() == "C":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = _d1(S, K, T, r, sigma)
    if opt_type.upper() == "C":
        return norm_cdf(d1)
    return norm_cdf(d1) - 1.0


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes Gamma (same for calls and puts)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    return norm_pdf(d1) / (S * sigma * math.sqrt(T))


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes Vega — ₹ per 1% IV move (divided by 100)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    return S * norm_pdf(d1) * math.sqrt(T) / 100.0


def bs_theta(S: float, K: float, T: float, r: float, sigma: float,
             opt_type: str = "C") -> float:
    """Black-Scholes Theta — ₹/day (negative for long options)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    decay = -(S * norm_pdf(d1) * sigma) / (2.0 * math.sqrt(T))
    if opt_type.upper() == "C":
        return (decay - r * K * math.exp(-r * T) * norm_cdf(d2)) / 365.0
    return (decay + r * K * math.exp(-r * T) * norm_cdf(-d2)) / 365.0


def bs_rho(S: float, K: float, T: float, r: float, sigma: float,
           opt_type: str = "C") -> float:
    """Black-Scholes Rho — ₹ per 1% change in risk-free rate."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d2 = _d2(S, K, T, r, sigma)
    if opt_type.upper() == "C":
        return K * T * math.exp(-r * T) * norm_cdf(d2) / 100.0
    return -K * T * math.exp(-r * T) * norm_cdf(-d2) / 100.0


# ---------------------------------------------------------------------------
# IV skew
# ---------------------------------------------------------------------------

_IV_SKEW_FLOOR = 0.08  # shared default floor, also used by enrich()'s vectorized skew calc


def get_iv_skew(K: float, S: float, base_iv: float,
                skew_slope: float = -0.0002,
                skew_floor: float = _IV_SKEW_FLOOR) -> float:
    """Moneyness-based IV skew approximation.

    adjusted_iv = base_iv + skew_slope * (K - S), floored at skew_floor.
    Default skew_slope (-0.0002) ≈ 2% IV increase per 100-pt move ITM on NIFTY.
    """
    if base_iv <= 0:
        base_iv = DEFAULT_BASE_IV
    return max(base_iv + skew_slope * (K - S), skew_floor)


# ---------------------------------------------------------------------------
# IV solver
# ---------------------------------------------------------------------------

def solve_iv(market_price: float, S: float, K: float, T: float, r: float,
             opt_type: str = "C",
             init_guess: float = DEFAULT_BASE_IV) -> float:
    """Newton-Raphson implied volatility solver.

    Returns IV as decimal, or DEFAULT_BASE_IV on failure (degenerate input,
    price below intrinsic, or no convergence in _IV_SOLVE_ITERS iterations).
    """
    if T <= 0 or S <= 0 or K <= 0:
        return DEFAULT_BASE_IV
    intrinsic = max(S - K, 0.0) if opt_type.upper() == "C" else max(K - S, 0.0)
    if market_price <= intrinsic:
        return DEFAULT_BASE_IV

    sigma = max(min(init_guess, _IV_MAX), _IV_MIN)
    for _ in range(_IV_SOLVE_ITERS):
        price_fn = bs_call if opt_type.upper() == "C" else bs_put
        price    = price_fn(S, K, T, r, sigma)
        vega_val = bs_vega(S, K, T, r, sigma) * 100.0   # undo /100 from bs_vega
        if abs(vega_val) < 1e-10:
            break
        diff  = price - market_price
        sigma -= diff / vega_val
        sigma  = max(min(sigma, _IV_MAX), _IV_MIN)
        if abs(diff) < _IV_SOLVE_TOL:
            return sigma

    return sigma if _IV_MIN < sigma < _IV_MAX else DEFAULT_BASE_IV


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------

def get_atm_iv(chain: pd.DataFrame, atm_strike: float,
               strike_col: str = "StrikePrice",
               ce_iv_col: str = "CE_IV") -> float:
    """Extract ATM call IV from a chain DataFrame. Returns DEFAULT_BASE_IV on miss."""
    if chain is None or chain.empty:
        return DEFAULT_BASE_IV
    row = chain[chain[strike_col] == atm_strike]
    if row.empty:
        return DEFAULT_BASE_IV
    iv = row[ce_iv_col].iloc[0]
    if iv is None or (isinstance(iv, float) and math.isnan(iv)):
        return DEFAULT_BASE_IV
    iv = float(iv)
    if iv > 1.0:          # stored as percentage (e.g. 14.2 → 0.142)
        iv = iv / 100.0
    return iv if iv > 0 else DEFAULT_BASE_IV


# ---------------------------------------------------------------------------
# Batch engine
# ---------------------------------------------------------------------------

class OptionChainEngine:
    """Batch Black-Scholes Greeks across a full NSE option chain DataFrame.

    Usage:
        engine = OptionChainEngine(spot=23000, dte=5, base_iv=0.142)
        chain  = engine.enrich(chain_df)   # adds Greek columns in-place

    Added columns:
        CE_Delta, CE_Gamma, CE_Theta, CE_Vega
        PE_Delta, PE_Gamma, PE_Theta, PE_Vega
        CE_IV_adj, PE_IV_adj   (skew-adjusted IV per strike)
        Net_GEX_B              (net gamma exposure in billions)
    """

    def __init__(self,
                 spot: float,
                 dte: int,
                 base_iv: float = DEFAULT_BASE_IV,
                 lot_size: int = 50,
                 risk_free: float = ANNUAL_RISK_FREE_RATE,
                 skew_slope: float = -0.0002):
        self.spot       = spot
        self.t          = max(dte / 365.0, _MIN_T_YEARS)
        self.base_iv    = base_iv if base_iv > 0 else DEFAULT_BASE_IV
        self.lot_size   = lot_size
        self.risk_free  = risk_free
        self.skew_slope = skew_slope

    def _iv(self, K: float) -> float:
        return get_iv_skew(K, self.spot, self.base_iv, self.skew_slope)

    def enrich(self, chain: pd.DataFrame,
               strike_col: str = "StrikePrice",
               ce_oi_col:  str = "CE_OI",
               pe_oi_col:  str = "PE_OI") -> pd.DataFrame:
        """Add Greek columns to `chain` and return it.

        Vectorized over the whole chain with numpy/scipy.special.ndtr —
        this used to be a per-row Python loop calling the scalar bs_delta/
        bs_gamma/bs_theta/bs_vega functions once per strike, per tick, per
        expiry. Same class of cost oi_analysis.calculate_greeks_vectorized
        already eliminated elsewhere; this brings enrich() in line with
        that so there's one fast path instead of two divergent ones.
        The scalar bs_* functions are untouched and still used for
        single-point lookups (atm_greeks(), solve_iv(), scenario P&L).
        """
        if chain is None or chain.empty:
            return chain

        S, T, r = self.spot, self.t, self.risk_free
        K = pd.to_numeric(chain[strike_col], errors="coerce").to_numpy(dtype=float)

        # Same skew curve as get_iv_skew(), vectorized: iv = max(base_iv + slope*(K-S), floor)
        iv = np.maximum(self.base_iv + self.skew_slope * (K - S), _IV_SKEW_FLOOR)

        valid = (T > 0) & (iv > 0) & (S > 0) & (K > 0)
        sqrt_T = math.sqrt(T) if T > 0 else 0.0

        with np.errstate(divide="ignore", invalid="ignore"):
            d1 = np.where(
                valid,
                (np.log(np.where(K > 0, S / K, 1.0)) + (r + 0.5 * iv ** 2) * T) / (iv * sqrt_T),
                0.0,
            )
        d2 = d1 - iv * sqrt_T

        cdf_d1, cdf_neg_d1 = ndtr(d1), ndtr(-d1)
        cdf_d2, cdf_neg_d2 = ndtr(d2), ndtr(-d2)
        pdf_d1 = np.exp(-0.5 * d1 ** 2) / _SQRT_2PI

        # Degenerate (T<=0 / iv<=0) fallback matches the scalar bs_delta()'s
        # intrinsic-based edge case: delta -> 1/0 (call) or -1/0 (put).
        ce_delta = np.where(valid, cdf_d1, np.where(S > K, 1.0, 0.0))
        pe_delta = np.where(valid, cdf_d1 - 1.0, np.where(S < K, -1.0, 0.0))

        gamma = np.where(valid, pdf_d1 / (S * iv * sqrt_T), 0.0)

        vega = np.where(valid, S * pdf_d1 * sqrt_T / 100.0, 0.0)

        decay = np.where(valid, -(S * pdf_d1 * iv) / (2.0 * sqrt_T), 0.0)
        disc  = math.exp(-r * T)
        ce_theta = np.where(valid, (decay - r * K * disc * cdf_d2) / 365.0, 0.0)
        pe_theta = np.where(valid, (decay + r * K * disc * cdf_neg_d2) / 365.0, 0.0)

        c_oi = pd.to_numeric(chain.get(ce_oi_col, 0), errors="coerce").fillna(0).to_numpy(dtype=float)
        p_oi = pd.to_numeric(chain.get(pe_oi_col, 0), errors="coerce").fillna(0).to_numpy(dtype=float)
        net_gex = (c_oi - p_oi) * gamma * self.lot_size * S / 1_000_000_000

        chain = chain.copy()
        chain["CE_IV_adj"] = iv;        chain["PE_IV_adj"] = iv
        chain["CE_Delta"]  = ce_delta;  chain["CE_Gamma"]  = gamma
        chain["CE_Theta"]  = ce_theta;  chain["CE_Vega"]   = vega
        chain["PE_Delta"]  = pe_delta;  chain["PE_Gamma"]  = gamma
        chain["PE_Theta"]  = pe_theta;  chain["PE_Vega"]   = vega
        chain["Net_GEX_B"] = net_gex
        return chain

    def atm_greeks(self, atm_strike: float) -> dict:
        """Lot-adjusted ATM Greeks dict (mirrors DashContext fields)."""
        S, K, T, r = self.spot, atm_strike, self.t, self.risk_free
        iv = self._iv(K)
        return {
            "atm_delta": bs_delta(S, K, T, r, iv, "C"),
            "atm_gamma": bs_gamma(S, K, T, r, iv) * self.lot_size * S / 100.0,
            "atm_vega":  bs_vega( S, K, T, r, iv) * self.lot_size,
            "atm_theta": abs(bs_theta(S, K, T, r, iv, "C") * self.lot_size),
            "ce_premium": bs_call(S, K, T, r, iv),
            "pe_premium": bs_put( S, K, T, r, iv),
        }



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
                          dte: int, lot_size: int) -> pd.DataFrame:
    """Per-strike CE/PE Greeks + Net GEX over the full master chain, computed once.
    Mirrors greeks_dashboard.render_greeks_section's prior inline math
    exactly (same skew curve, same gamma-on-CE-IV-for-both-legs convention)
    so chart/table output doesn't shift."""
    # master (oi_analysis.build_master_table_nse output) uses lowercase
    # snake_case columns ('strike'/'ce_oi'/'pe_oi'); window uses the raw
    # NSE-parser columns ('StrikePrice'/'CE_OI'/'PE_OI').
    strike_col = 'strike' if 'strike' in df.columns else 'StrikePrice'
    ce_oi_col  = 'ce_oi'  if 'ce_oi'  in df.columns else 'CE_OI'
    pe_oi_col  = 'pe_oi'  if 'pe_oi'  in df.columns else 'PE_OI'
    strikes = df[strike_col].tolist()
    ce_oi = df[ce_oi_col].fillna(0).tolist()
    pe_oi = df[pe_oi_col].fillna(0).tolist()
    t_param = max(dte / 365.0, _MIN_T_YEARS)
    r_param = ANNUAL_RISK_FREE_RATE

    rows = []
    for i, k in enumerate(strikes):
        iv = get_iv_skew(k, spot, base_iv)
        c_d = bs_delta(spot, k, t_param, r_param, iv, "C")
        p_d = bs_delta(spot, k, t_param, r_param, iv, "P")
        c_g = bs_gamma(spot, k, t_param, r_param, iv)
        c_t = bs_theta(spot, k, t_param, r_param, iv, "C")
        p_t = bs_theta(spot, k, t_param, r_param, iv, "P")
        c_v = bs_vega(spot, k, t_param, r_param, iv)
        gex_val = (ce_oi[i] - pe_oi[i]) * c_g * lot_size * spot / 1_000_000_000

        rows.append({
            'Strike': k,
            'cDelta': c_d, 'cGamma': c_g, 'cTheta': c_t, 'cVega': c_v,
            # NOTE: pGamma/pVega intentionally reuse the CE-side gamma/vega
            # (c_g / c_v), matching greeks_dashboard.py's prior behavior
            # (pg.append(c_g), pv.append(c_v)) exactly — preserved as-is so
            # this refactor doesn't silently change displayed numbers.
            'pDelta': p_d, 'pGamma': c_g, 'pTheta': p_t, 'pVega': c_v,
            'netGEX': gex_val,
            'iv': iv,
        })
    return pd.DataFrame(rows)


# ===========================================================================
# Strategy definitions + pricing (was inline inside
# dashboard_modules.build_strat_defs, mixed with no Excel calls there
# already — but recomputing the same atm_c/atm_p/otm_c/... Black-Scholes
# prices that greeks_dashboard/iv_surface/engine all separately compute too)
# ===========================================================================

def _build_strategies(spot: float, atm: float, step: int, dte: int,
                        base_iv: float, lot_size: int,
                        near_expiry: str = "", far_expiry: str = "") -> list[dict]:
    t_param = max(dte / 365.0, _MIN_T_YEARS)
    r_param = ANNUAL_RISK_FREE_RATE

    atm_c  = bs_call(spot, atm,          t_param, r_param, base_iv)
    atm_p  = bs_put( spot, atm,          t_param, r_param, base_iv)
    otm_c  = bs_call(spot, atm + step*3, t_param, r_param, get_iv_skew(atm + step*3, spot, base_iv))
    otm_p  = bs_put( spot, atm - step*3, t_param, r_param, get_iv_skew(atm - step*3, spot, base_iv))
    wing_c = bs_call(spot, atm + step*7, t_param, r_param, get_iv_skew(atm + step*7, spot, base_iv))
    wing_p = bs_put( spot, atm - step*7, t_param, r_param, get_iv_skew(atm - step*7, spot, base_iv))
    far_c  = bs_call(spot, atm, t_param * 2, r_param, base_iv * 1.02)

    # fmt_k imported from ui_theme via the module-level import below
    strats = {}

    net_cost_bcs = atm_c - otm_c
    strats['BCS'] = {
        'name': "Bull Call Spread", 'type_code': "BCS", 'risk_level': "Low",
        'color_key': 'CLR_INFO',
        'tags': ["[Bullish]", "[Debit]", "[Defined Risk]"],
        'desc': "Buy ATM call, sell OTM call. Best when IV is low and outlook is moderately bullish.",
        'legs': [f"Buy {atm:,.0f} CE @ \u20b9{atm_c:.1f}",
                 f"Sell {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}", "", ""],
        'max_profit': _fmt_k((step*3 - net_cost_bcs) * lot_size),
        'max_loss':   _fmt_k(net_cost_bcs * lot_size),
        'breakeven':  f"{atm + net_cost_bcs:,.0f}",
        'rr':         f"{(step*3 - net_cost_bcs) / max(net_cost_bcs, 1.0):.1f}:1",
    }

    ic_prem = otm_c + otm_p - wing_c - wing_p
    strats['IC'] = {
        'name': "Iron Condor", 'type_code': "IC", 'risk_level': "Moderate",
        'color_key': 'CLR_SUCCESS',
        'tags': ["[Neutral]", "[Credit]", "[Range-bound]"],
        'desc': "Sell OTM strangle, buy farther OTM wings. Ideal for IV Rank >60.",
        'legs': [f"Sell {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}",
                 f"Buy {atm+step*7:,.0f} CE @ \u20b9{wing_c:.1f}",
                 f"Sell {atm-step*3:,.0f} PE @ \u20b9{otm_p:.1f}",
                 f"Buy {atm-step*7:,.0f} PE @ \u20b9{wing_p:.1f}"],
        'max_profit': _fmt_k(ic_prem * lot_size),
        'max_loss':   _fmt_k((step*4 - ic_prem) * lot_size),
        'breakeven':  f"{atm-step*3-ic_prem:,.0f} / {atm+step*3+ic_prem:,.0f}",
        'rr':         f"{ic_prem / max(step*4 - ic_prem, 1.0):.1f}:1",
    }

    net_cost_bps = atm_p - otm_p
    strats['BPS'] = {
        'name': "Bear Put Spread", 'type_code': "BPS", 'risk_level': "Low",
        'color_key': 'CLR_DN',
        'tags': ["[Bearish]", "[Debit]", "[Defined Risk]"],
        'desc': "Buy ATM put, sell OTM put. Effective when max pain < CMP and PCR rising.",
        'legs': [f"Buy {atm:,.0f} PE @ \u20b9{atm_p:.1f}",
                 f"Sell {atm-step*3:,.0f} PE @ \u20b9{otm_p:.1f}", "", ""],
        'max_profit': _fmt_k((step*3 - net_cost_bps) * lot_size),
        'max_loss':   _fmt_k(net_cost_bps * lot_size),
        'breakeven':  f"{atm - net_cost_bps:,.0f}",
        'rr':         f"{(step*3 - net_cost_bps) / max(net_cost_bps, 1.0):.1f}:1",
    }

    ss_prem = atm_c + atm_p
    strats['SS'] = {
        'name': "Short Straddle", 'type_code': "SS", 'risk_level': "High",
        'color_key': 'CLR_WARN',
        'tags': ["[Neutral]", "[Credit]", "[High IV]"],
        'desc': "Sell ATM call + put. Aggressive theta capture when IV Rank >70. Unlimited risk.",
        'legs': [f"Sell {atm:,.0f} CE @ \u20b9{atm_c:.1f}",
                 f"Sell {atm:,.0f} PE @ \u20b9{atm_p:.1f}", "", ""],
        'max_profit': _fmt_k(ss_prem * lot_size),
        'max_loss':   "Unlimited",
        'breakeven':  f"{atm-ss_prem:,.0f} / {atm+ss_prem:,.0f}",
        'rr':         "N/A",
    }

    cal_cost = far_c - atm_c
    # Use actual expiry date strings when available; fall back to descriptive labels
    _near_lbl = near_expiry if near_expiry else "NEAR"
    _far_lbl  = far_expiry  if far_expiry  else "FAR"
    strats['CAL'] = {
        'name': "Calendar Spread", 'type_code': "CAL", 'risk_level': "Low",
        'color_key': 'CLR_INFO',
        'tags': ["[Neutral]", "[Debit]", "[Theta Change]"],
        'desc': "Buy far-month ATM call, sell near-month. Profits from horizontal decay-skew differentials.",
        'legs': [
            {'action': 'SELL', 'type': 'CE', 'strike': int(atm), 'ltp': round(atm_c, 1), 'expiry': _near_lbl, 'lots': 1},
            {'action': 'BUY',  'type': 'CE', 'strike': int(atm), 'ltp': round(far_c, 1), 'expiry': _far_lbl,  'lots': 1},
        ],
        'max_profit': _fmt_k(cal_cost * lot_size * 2),
        'max_loss':   _fmt_k(cal_cost * lot_size),
        'breakeven':  f"{atm-step*2:,.0f} / {atm+step*2:,.0f}",
        'rr':         "2.0:1",
    }

    rps_prem = atm_p - 2 * otm_p
    strats['RPS'] = {
        'name': "Ratio Put Spread", 'type_code': "RPS", 'risk_level': "High",
        'color_key': 'CLR_WARN',
        'tags': ["[Bearish]", "[Credit/Debit]" if rps_prem >= 0 else "[Net Debit]", "[Complex]"],
        'desc': "Buy 1 ATM put, sell 2 OTM puts. Profits in moderate fall; watch large down-tail risk.",
        'legs': [f"Buy 1x {atm:,.0f} PE @ \u20b9{atm_p:.1f}",
                 f"Sell 2x {atm-step*3:,.0f} PE @ \u20b9{otm_p:.1f}", "", ""],
        'max_profit': _fmt_k((step*3 + rps_prem) * lot_size),
        'max_loss':   f"Unlimited below {atm-step*6:,.0f}",
        'breakeven':  f"{atm - rps_prem:,.0f} (Upper)",
        'rr':         "Varies",
    }

    # ── Covered Call ─────────────────────────────────────────────────────
    strats['CC'] = {
        'name': "Covered Call", 'type_code': "CC", 'risk_level': "Low",
        'color_key': 'CLR_SUCCESS',
        'tags': ["[Neutral]", "[Income]", "[Requires Underlying]"],
        'desc': "Hold the underlying, sell an OTM call against it. Caps upside for steady income.",
        'legs': [f"Buy Underlying @ \u20b9{spot:,.1f}",
                 f"Sell {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}", "", ""],
        'max_profit': _fmt_k((atm+step*3 - spot + otm_c) * lot_size),
        'max_loss':   f"Substantial below \u20b9{spot-otm_c:,.0f}",
        'breakeven':  f"{spot-otm_c:,.0f}",
        'rr':         "Varies",
    }

    # ── Butterfly Spread (long call butterfly) ──────────────────────────
    itm_c = bs_call(spot, atm - step*3, t_param, r_param, get_iv_skew(atm - step*3, spot, base_iv))
    bfly_cost = itm_c + otm_c - 2*atm_c
    strats['BFLY'] = {
        'name': "Butterfly Spread", 'type_code': "BFLY", 'risk_level': "Low",
        'color_key': 'CLR_INFO',
        'tags': ["[Neutral]", "[Debit]", "[Pin Risk]"],
        'desc': "Buy 1 ITM call, sell 2 ATM calls, buy 1 OTM call. Best when spot pins near the body strike.",
        'legs': [f"Buy 1x {atm-step*3:,.0f} CE @ \u20b9{itm_c:.1f}",
                 f"Sell 2x {atm:,.0f} CE @ \u20b9{atm_c:.1f}",
                 f"Buy 1x {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}", ""],
        'max_profit': _fmt_k((step*3 - bfly_cost) * lot_size),
        'max_loss':   _fmt_k(bfly_cost * lot_size),
        'breakeven':  f"{atm-step*3+bfly_cost:,.0f} / {atm+step*3-bfly_cost:,.0f}",
        'rr':         f"{(step*3 - bfly_cost) / max(bfly_cost, 1.0):.1f}:1",
    }

    # ── Bull Put Spread (credit) ─────────────────────────────────────────
    bups_credit = otm_p - wing_p
    strats['BUPS'] = {
        'name': "Bull Put Spread", 'type_code': "BUPS", 'risk_level': "Low",
        'color_key': 'CLR_SUCCESS',
        'tags': ["[Bullish]", "[Credit]", "[Defined Risk]"],
        'desc': "Sell higher-strike put, buy lower-strike put for protection. Collects premium if spot holds above the short strike.",
        'legs': [f"Sell {atm-step*3:,.0f} PE @ \u20b9{otm_p:.1f}",
                 f"Buy {atm-step*7:,.0f} PE @ \u20b9{wing_p:.1f}", "", ""],
        'max_profit': _fmt_k(bups_credit * lot_size),
        'max_loss':   _fmt_k((step*4 - bups_credit) * lot_size),
        'breakeven':  f"{atm-step*3-bups_credit:,.0f}",
        'rr':         f"{bups_credit / max(step*4 - bups_credit, 1.0):.1f}:1",
    }

    # ── Bear Call Spread (credit) ─────────────────────────────────────────
    becs_credit = otm_c - wing_c
    strats['BECS'] = {
        'name': "Bear Call Spread", 'type_code': "BECS", 'risk_level': "Low",
        'color_key': 'CLR_DN',
        'tags': ["[Bearish]", "[Credit]", "[Defined Risk]"],
        'desc': "Sell lower-strike call, buy higher-strike call for protection. Collects premium if spot stays below the short strike.",
        'legs': [f"Sell {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}",
                 f"Buy {atm+step*7:,.0f} CE @ \u20b9{wing_c:.1f}", "", ""],
        'max_profit': _fmt_k(becs_credit * lot_size),
        'max_loss':   _fmt_k((step*4 - becs_credit) * lot_size),
        'breakeven':  f"{atm+step*3+becs_credit:,.0f}",
        'rr':         f"{becs_credit / max(step*4 - becs_credit, 1.0):.1f}:1",
    }

    # ── Long Straddle ──────────────────────────────────────────────────
    ls_cost = atm_c + atm_p
    strats['LS'] = {
        'name': "Long Straddle", 'type_code': "LS", 'risk_level': "Moderate",
        'color_key': 'CLR_INFO',
        'tags': ["[Neutral]", "[Debit]", "[Low IV]"],
        'desc': "Buy ATM call + ATM put. Profits from a big move either way; best entered when IV Rank is low.",
        'legs': [f"Buy {atm:,.0f} CE @ \u20b9{atm_c:.1f}",
                 f"Buy {atm:,.0f} PE @ \u20b9{atm_p:.1f}", "", ""],
        'max_profit': "Unlimited",
        'max_loss':   _fmt_k(ls_cost * lot_size),
        'breakeven':  f"{atm-ls_cost:,.0f} / {atm+ls_cost:,.0f}",
        'rr':         "N/A",
    }

    # ── Long Strangle ──────────────────────────────────────────────────
    lsg_cost = otm_c + otm_p
    strats['LSG'] = {
        'name': "Long Strangle", 'type_code': "LSG", 'risk_level': "Moderate",
        'color_key': 'CLR_INFO',
        'tags': ["[Neutral]", "[Debit]", "[Low IV]"],
        'desc': "Buy OTM call + OTM put. Cheaper than a straddle; needs a bigger move to profit.",
        'legs': [f"Buy {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}",
                 f"Buy {atm-step*3:,.0f} PE @ \u20b9{otm_p:.1f}", "", ""],
        'max_profit': "Unlimited",
        'max_loss':   _fmt_k(lsg_cost * lot_size),
        'breakeven':  f"{atm-step*3-lsg_cost:,.0f} / {atm+step*3+lsg_cost:,.0f}",
        'rr':         "N/A",
    }

    # ── Long Call ─────────────────────────────────────────────────────
    strats['LC'] = {
        'name': "Long Call", 'type_code': "LC", 'risk_level': "Moderate",
        'color_key': 'CLR_SUCCESS',
        'tags': ["[Bullish]", "[Debit]", "[Unlimited Upside]"],
        'desc': "Buy a single ATM call. Pure directional bet on a strong upward move.",
        'legs': [f"Buy {atm:,.0f} CE @ \u20b9{atm_c:.1f}", "", "", ""],
        'max_profit': "Unlimited",
        'max_loss':   _fmt_k(atm_c * lot_size),
        'breakeven':  f"{atm+atm_c:,.0f}",
        'rr':         "N/A",
    }

    # ── Long Put ──────────────────────────────────────────────────────
    strats['LP'] = {
        'name': "Long Put", 'type_code': "LP", 'risk_level': "Moderate",
        'color_key': 'CLR_DN',
        'tags': ["[Bearish]", "[Debit]", "[Defined Risk]"],
        'desc': "Buy a single ATM put. Pure directional bet on a strong downward move.",
        'legs': [f"Buy {atm:,.0f} PE @ \u20b9{atm_p:.1f}", "", "", ""],
        'max_profit': _fmt_k(max(atm - atm_p, 0) * lot_size) + " (if spot\u21920)",
        'max_loss':   _fmt_k(atm_p * lot_size),
        'breakeven':  f"{atm-atm_p:,.0f}",
        'rr':         "N/A",
    }

    # ── Protective Put ────────────────────────────────────────────────
    strats['PP'] = {
        'name': "Protective Put", 'type_code': "PP", 'risk_level': "Low",
        'color_key': 'CLR_SUCCESS',
        'tags': ["[Bullish]", "[Debit]", "[Requires Underlying]"],
        'desc': "Hold the underlying, buy an ATM put as insurance. Locks in a floor while leaving upside open.",
        'legs': [f"Buy Underlying @ \u20b9{spot:,.1f}",
                 f"Buy {atm:,.0f} PE @ \u20b9{atm_p:.1f}", "", ""],
        'max_profit': "Unlimited",
        'max_loss':   _fmt_k((atm_p + max(spot-atm, 0)) * lot_size),
        'breakeven':  f"{spot+atm_p:,.0f}",
        'rr':         "N/A",
    }

    return list(strats.values())


# Directional lean of each strategy type, used to reconcile against
# _detect_traps()'s trap_str and the OI-buildup `bias` (combined_view).
# "long_vol" = direction-agnostic (profits from a big move either way), so
# it is not penalized by BULL_TRAP/BEAR_TRAP and is the one category that
# benefits from SQUEEZE rather than being dampened by it.
_STRATEGY_DIRECTION = {
    "BCS": "bullish", "BUPS": "bullish", "LC": "bullish", "CC": "bullish",
    "BPS": "bearish", "BECS": "bearish", "LP": "bearish", "RPS": "bearish",
    "IC": "neutral", "SS": "neutral", "CAL": "neutral", "BFLY": "neutral",
    "PP": "neutral",
    "LS": "long_vol", "LSG": "long_vol",
}

# Max attainable raw score per strategy code (sum of every positive branch
# for that code in _score_strategies), used to normalize scores onto a
# comparable 0-100 scale — otherwise a structurally-higher-ceiling strategy
# (e.g. SS caps at 10) always looks more "confident" than a lower-ceiling
# one (e.g. PP caps at 5) regardless of actual fit.
_STRATEGY_MAX_SCORE = {
    "BCS": 10, "IC": 10, "BPS": 9, "SS": 10, "CAL": 8, "RPS": 8,
    "CC": 7, "BFLY": 8, "BUPS": 9, "BECS": 9, "LS": 10, "LSG": 9,
    "LC": 8, "LP": 8, "PP": 5,
}


def _score_strategies(strats: list[dict], spot: float, atm: float,
                        pcr: float, iv_rank: float, dte: int,
                        bias: str = "Mixed / Neutral",
                        trap_str: str = "BALANCED",
                        trade_grade: str = "A") -> list[dict]:
    """
    Returns one dict per strategy in `strats`:
      {'score': raw int, 'confidence_pct': 0-100 normalized score after
       trap/bias reconciliation, 'veto_reasons': [str, ...]}

    Reconciliation step (this is the part that used to be missing): a
    strategy's raw rule-based score says nothing about whether it conflicts
    with the trap detector or the OI-buildup bias computed elsewhere in this
    same engine run. Without this step, a directional strategy could score
    highest and still be recommended directly into an active BULL_TRAP/
    BEAR_TRAP, or against a "Strong Bullish"/"Strong Bearish" bias — which
    is exactly the kind of mismatch that produces confident-looking losers.
    """
    results = []
    for s in strats:
        sc = 0
        code = s['type_code']
        if code == "BCS":
            if spot >= atm: sc += 3
            if pcr < 0.9: sc += 2
            if iv_rank < 50: sc += 2
            if dte > 7: sc += 1
            sc += 2
        elif code == "IC":
            if iv_rank > 60: sc += 4
            if 0.9 < pcr < 1.2: sc += 3
            if dte > 10: sc += 2
            sc += 1
        elif code == "BPS":
            if spot < atm: sc += 3
            if pcr < 0.8: sc += 2
            if iv_rank < 50: sc += 2
            if dte > 7: sc += 1
            sc += 1
        elif code == "SS":
            if iv_rank > 70: sc += 5
            if 0.9 < pcr < 1.1: sc += 3
            if dte > 15: sc += 2
        elif code == "CAL":
            if dte < 10: sc += 4
            if iv_rank < 40: sc += 3
            sc += 1
        elif code == "RPS":
            if spot < atm: sc += 3
            if iv_rank > 55: sc += 3
            if dte > 10: sc += 2
        elif code == "CC":
            if -0.9 < pcr < 1.1: sc += 2  # roughly flat-to-mildly-bullish outlook
            if iv_rank > 45: sc += 3      # richer premium to sell against the holding
            if dte > 7: sc += 1
            sc += 1
        elif code == "BFLY":
            if abs(spot - atm) < dte:     # spot already pinned near ATM
                sc += 3
            if iv_rank < 45: sc += 3
            if dte < 15: sc += 2
        elif code == "BUPS":
            if spot >= atm: sc += 3
            if iv_rank > 45: sc += 3      # credit strategies want richer premium
            if pcr < 1.0: sc += 2
            if dte > 5: sc += 1
        elif code == "BECS":
            if spot < atm: sc += 3
            if iv_rank > 45: sc += 3
            if pcr > 1.0: sc += 2
            if dte > 5: sc += 1
        elif code == "LS":
            if iv_rank < 40: sc += 5      # buying vega — want it cheap
            if 0.9 < pcr < 1.1: sc += 3
            if dte > 10: sc += 2
        elif code == "LSG":
            if iv_rank < 40: sc += 4
            if 0.9 < pcr < 1.1: sc += 2
            if dte > 10: sc += 2
            sc += 1                       # cheaper entry than a straddle
        elif code == "LC":
            if spot >= atm: sc += 3
            if pcr < 0.9: sc += 2
            if iv_rank < 45: sc += 2      # cheaper premium to buy
            if dte > 5: sc += 1
        elif code == "LP":
            if spot < atm: sc += 3
            if pcr > 1.1: sc += 2
            if iv_rank < 45: sc += 2
            if dte > 5: sc += 1
        elif code == "PP":
            if spot >= atm: sc += 2       # already holding, protecting gains
            if iv_rank < 50: sc += 2      # cheaper insurance
            sc += 1

        direction = _STRATEGY_DIRECTION.get(code, "neutral")
        veto_reasons: list[str] = []
        multiplier = 1.0

        # ── Trap reconciliation ─────────────────────────────────────────
        if trap_str == "BULL_TRAP" and direction == "bullish":
            multiplier *= 0.25
            veto_reasons.append("Conflicts with active BULL_TRAP — chasing upside into a wall.")
        elif trap_str == "BEAR_TRAP" and direction == "bearish":
            multiplier *= 0.25
            veto_reasons.append("Conflicts with active BEAR_TRAP — chasing downside into a wall.")
        elif trap_str == "SQUEEZE" and direction in ("bullish", "bearish", "neutral"):
            # Range-bound premium sellers and one-sided directional bets are
            # both exposed to a squeeze resolving hard in either direction;
            # only long_vol strategies are structurally suited to it.
            multiplier *= 0.6
            veto_reasons.append("OI squeeze active — range/directional bets both exposed to a breakout.")

        # ── Bias (OI buildup conviction) reconciliation ─────────────────
        if bias == "Strong Bullish" and direction == "bearish":
            multiplier *= 0.35
            veto_reasons.append("OI buildup shows Strong Bullish conviction — contradicts bearish strategy.")
        elif bias == "Bullish" and direction == "bearish":
            multiplier *= 0.6
            veto_reasons.append("OI buildup leans Bullish — contradicts bearish strategy.")
        elif bias == "Strong Bearish" and direction == "bullish":
            multiplier *= 0.35
            veto_reasons.append("OI buildup shows Strong Bearish conviction — contradicts bullish strategy.")
        elif bias == "Bearish" and direction == "bullish":
            multiplier *= 0.6
            veto_reasons.append("OI buildup leans Bearish — contradicts bullish strategy.")

        # ── Overall setup risk (trade_grade) — dampens everything, since a
        # C/D grade means elevated VIX and/or multiple traps stacked, not
        # just one specific directional conflict ─────────────────────────
        if trade_grade == "C":
            multiplier *= 0.85
        elif trade_grade == "D":
            multiplier *= 0.6
            veto_reasons.append(f"Trade grade {trade_grade} — elevated overall setup risk.")

        max_score = _STRATEGY_MAX_SCORE.get(code, max(sc, 1))
        confidence_pct = round(min((sc / max_score) * 100.0, 100.0) * multiplier)

        results.append({
            'score': sc,
            'confidence_pct': confidence_pct,
            'veto_reasons': veto_reasons,
        })
    return results




# ===========================================================================
# Scenario P&L (was inline inside dashboard_modules.render_risk_dashboard /
# the now-deleted dashboard_intelligence.py duplicate)
# ===========================================================================

def _build_scenario_pnl(spot: float, atm_delta: float, lot_size: int) -> list[dict]:
    scenarios = [-0.05, -0.03, -0.01, 0.0, 0.01, 0.03, 0.05]
    labels = ["-5%", "-3%", "-1%", "Flat", "+1%", "+3%", "+5%"]
    out = []
    for label, shift in zip(labels, scenarios):
        sim_pnl = (shift * spot * atm_delta) * lot_size
        out.append({'label': label, 'shift': shift, 'pnl': sim_pnl})
    return out


# ===========================================================================
# Risk meters (was inline inside dashboard_modules.render_risk_dashboard)
# ===========================================================================

def _build_risk_meters(atm_delta: float, atm_gamma: float, base_iv: float,
                         atm_theta: float, lot_size: int, dte: int,
                         pcr: float) -> list[dict]:
    return [
        {'name': "Delta Risk",     'pct': int(min(abs(atm_delta) * 100, 100))},
        {'name': "Gamma Risk",     'pct': int(min(abs(atm_gamma) * 100_000, 100))},
        {'name': "Vega Risk",      'pct': int(min(base_iv * 333, 100))},
        {'name': "Theta Decay",    'pct': int(min(abs(atm_theta) * 5, 100))},
        {'name': "Liquidity Risk", 'pct': 60 if lot_size > 100 else 25},
        {'name': "Event Risk",     'pct': 85 if dte <= 3 else 55 if dte <= 7 else 30},
        {'name': "Concentration",  'pct': int(min(40 / max(pcr, 0.5), 90))},
    ]


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

from mTerminals_json import _safe_num

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


# ===========================================================================
# Trap detector  (replaces hardcoded stubs in build_engine_result)
# ===========================================================================

def _detect_traps(
    spot: float,
    atm: float,
    ce_wall: float,
    pe_wall: float,
    strike_step: int,
    total_pcr: float,
    base_iv: float,
    india_vix: float,
    vel_df: "pd.DataFrame | None",
    bull_trap_iv_spike: float = 0.03,
    bear_trap_pcr_min:  float = 0.80,
    wall_proximity_pts: int   = 2,
) -> dict:
    traps_active: list[str] = []
    warnings:     list[str] = []

    ce_writing_near_wall = False
    pe_writing_near_wall = False
    both_sides_building  = False

    if vel_df is not None and not vel_df.empty:
        for _, row in vel_df.iterrows():
            strike = float(row.get("Strike", 0))
            ce_doi = float(row.get("CE_OI_Delta", 0) or 0)
            pe_doi = float(row.get("PE_OI_Delta", 0) or 0)
            ce_oi  = float(row.get("CE_OI", 1) or 1)
            pe_oi  = float(row.get("PE_OI", 1) or 1)
            if abs(strike - ce_wall) <= strike_step * wall_proximity_pts and ce_doi > 0 and ce_doi / ce_oi > 0.05:
                ce_writing_near_wall = True
            if abs(strike - pe_wall) <= strike_step * wall_proximity_pts and pe_doi > 0 and pe_doi / pe_oi > 0.05:
                pe_writing_near_wall = True
        net_ce = vel_df["CE_OI_Delta"].fillna(0).sum() if "CE_OI_Delta" in vel_df.columns else 0
        net_pe = vel_df["PE_OI_Delta"].fillna(0).sum() if "PE_OI_Delta" in vel_df.columns else 0
        both_sides_building = (net_ce > 0 and net_pe > 0)

    ce_dist_pts  = ce_wall - spot
    pe_dist_pts  = spot - pe_wall
    near_ce_wall = 0 < ce_dist_pts <= strike_step * wall_proximity_pts
    near_pe_wall = 0 < pe_dist_pts <= strike_step * wall_proximity_pts
    tight_channel = (ce_wall - pe_wall) <= strike_step * 4 and ce_wall > spot > pe_wall

    if near_ce_wall and ce_writing_near_wall and base_iv > (DEFAULT_BASE_IV + bull_trap_iv_spike):
        traps_active.append("BULL_TRAP")
        warnings.append(
            f"Bull trap risk — CE wall ₹{ce_wall:,.0f} only {ce_dist_pts:.0f} pts above; "
            f"CE OI building + IV elevated ({base_iv*100:.1f}%). Avoid chasing CE."
        )

    if near_pe_wall and pe_writing_near_wall and total_pcr < bear_trap_pcr_min:
        traps_active.append("BEAR_TRAP")
        warnings.append(
            f"Bear trap risk — PE wall ₹{pe_wall:,.0f} only {pe_dist_pts:.0f} pts below; "
            f"PE writers active + PCR {total_pcr:.2f}. Avoid naked PE shorts."
        )

    if abs(spot - atm) <= strike_step * 1.0:
        traps_active.append("PIN_RISK")
        warnings.append(
            f"Pin risk — spot ₹{spot:,.0f} within {abs(spot-atm):.0f} pts of ATM ₹{atm:,.0f}."
        )

    if tight_channel and both_sides_building:
        traps_active.append("SQUEEZE")
        warnings.append(
            f"OI squeeze — spot trapped CE ₹{ce_wall:,.0f} / PE ₹{pe_wall:,.0f} "
            f"({ce_wall - pe_wall:.0f} pts), both sides building."
        )

    ce_otm_dist = ce_wall - atm
    pe_otm_dist = atm - pe_wall
    atm_skew, skew_warn = 0.0, ""
    if ce_otm_dist > 0 and pe_otm_dist > 0:
        atm_skew = round((pe_otm_dist - ce_otm_dist) / max(pe_otm_dist + ce_otm_dist, 1), 3)
        if atm_skew > 0.15:
            skew_warn = (f"Put skew elevated ({atm_skew:.2f}) — OTM put IV bid heavy.")
        elif atm_skew < -0.15:
            skew_warn = (f"Call skew elevated ({atm_skew:.2f}) — unusual breakout positioning.")

    n_traps = len(traps_active)
    vix_penalty = 1 if india_vix > 18 else (2 if india_vix > 24 else 0)
    trade_grade = {0: "A", 1: "B", 2: "C"}.get(n_traps + vix_penalty, "D")

    return {
        "trap_str":    traps_active[0] if traps_active else "BALANCED",
        "trap_warn":   " | ".join(warnings) if warnings else "None",
        "skew_warn":   skew_warn,
        "atm_skew":    atm_skew,
        "trade_grade": trade_grade,
    }

# ===========================================================================
# EngineResult
# ===========================================================================

@dataclass
class EngineResult:
    # identity / context
    symbol: str
    expiry: str
    dte: int
    spot: float
    atm: float
    atm_idx: int
    strike_step: int
    lot_size: int
    base_iv: float

    # tables
    master: pd.DataFrame
    window: pd.DataFrame
    greeks_table: pd.DataFrame
    vel_df: "pd.DataFrame | None"

    # scalar derived values
    total_pcr: float
    oi_chg_pcr: float
    max_pain: float
    max_pain_dist: float
    ce_wall: float
    pe_wall: float
    atm_delta: float
    atm_theta: float
    atm_gamma: float
    atm_vega: float
    ce_premium: float
    pe_premium: float
    iv_rank: float
    hv30: float
    india_vix: float
    vix_regime: str
    basis: float
    fut_signal: str
    pcr_sentiment: str
    bias: str
    is_up: bool
    spot_change: float
    spot_chg_pct: float
    real_picture: str
    trap_str: str
    skew_warn: str
    atm_skew: float
    trade_grade: str
    trap_warn: str

    # derived structures
    india_vix_chg_pct: float = 0.0  # VIX's own % change vs prev close
    strategies: list = field(default_factory=list)
    strategy_scores: list = field(default_factory=list)
    scenario_pnl: list = field(default_factory=list)
    risk_meters: list = field(default_factory=list)
    smart_money_top: "pd.DataFrame | None" = None
    # Per-strike volume/OI ratios for DecisionEngine volume confirmation.
    # dict[str(strike)] → {'ce': float, 'pe': float, 'ce_vol': int, 'pe_vol': int}
    vol_oi_ratios: dict = field(default_factory=dict)
    wing_premiums: dict = field(default_factory=dict)
    # Real OTM wing LTPs at the exact strikes decision_engine.py's Bear Call
    # Spread / Bull Put Spread / Iron Condor / Long Strangle legs use
    # (atm ± 2*strike_step) — {"pe_buy": <PE_LTP at atm-2*step>,
    # "ce_buy": <CE_LTP at atm+2*step>}. Was previously never populated
    # (decision_engine.py's `getattr(er, "wing_premiums", None)` always hit
    # the None default), which meant those strategies' long/BUY leg always
    # priced at a fabricated 0.0 instead of its real premium — silently
    # blocking that leg from being executed as a paper order. Populated in
    # build_engine_result() below, once, from the same df_clean chain slice
    # everything else here already reads.
    near_expiry: str = ""   # NEAR slot date string from ExpiryManager
    far_expiry:  str = ""   # FAR/MONTHLY slot date string from ExpiryManager
    # Raw per-strike poll-to-poll snapshot (StrikePrice, CE_OI_Delta,
    # PE_OI_Delta, CE_Volume_Delta, PE_Volume_Delta, CE_IV_Delta,
    # PE_IV_Delta — exactly oi_analysis.build_oi_history()'s output
    # schema, which is also what build_training_warehouse.py trains on).
    # This is the real feature source for virtual_oi_estimator.py — do
    # not reconstruct these deltas from vel_df or ctx_dict elsewhere.
    oi_history_snapshot: "pd.DataFrame | None" = None

    def to_ctx_dict(self) -> dict:
        """Adapter so existing render_*.py functions written for a plain
        ctx: dict (dashboard_kpis.py, signals_dashboard.py, dashboard_modules.py,
        etc.) keep working with minimal/no signature changes during migration."""
        return {
            "symbol": self.symbol, "spot": self.spot, "atm": self.atm,
            "base_iv": self.base_iv, "dte": self.dte, "lot_size": self.lot_size,
            "strike_step": self.strike_step, "is_up": self.is_up,
            "spot_change": self.spot_change, "spot_chg_pct": self.spot_chg_pct,
            "bias": self.bias, "trade_grade": self.trade_grade,
            "ce_wall": self.ce_wall, "pe_wall": self.pe_wall,
            "total_pcr": self.total_pcr, "oi_chg_pcr": self.oi_chg_pcr,
            "max_pain": self.max_pain, "max_pain_dist": self.max_pain_dist,
            "atm_delta": self.atm_delta, "atm_theta": self.atm_theta,
            "atm_gamma": self.atm_gamma, "atm_vega": self.atm_vega,
            "basis": self.basis, "india_vix": self.india_vix,
            "vix_regime": self.vix_regime, "fut_signal": self.fut_signal,
            "india_vix_chg_pct": self.india_vix_chg_pct,
            "pcr_sentiment": self.pcr_sentiment, "real_picture": self.real_picture,
            "trap_str": self.trap_str, "skew_warn": self.skew_warn,
            "atm_skew": self.atm_skew, "ce_premium": self.ce_premium,
            "pe_premium": self.pe_premium, "iv_rank": self.iv_rank,
            "hv30": self.hv30, "trap_warn": self.trap_warn,
            "n_str": 10,
            # Derived structures (computed once in engine.py; render files
            # read these instead of recomputing Greeks/scoring/ranking).
            "strategies": self.strategies,
            "strategy_scores": self.strategy_scores,
            "scenario_pnl": self.scenario_pnl,
            "risk_meters": self.risk_meters,
            "smart_money_top": self.smart_money_top,
            "vol_oi_ratios": self.vol_oi_ratios,
            "wing_premiums": self.wing_premiums,
            "greeks_table": self.greeks_table,
            "window": self.window,
            "near_expiry": self.near_expiry,
            "far_expiry":  self.far_expiry,
        }


# ===========================================================================
# Orchestrator
# ===========================================================================

def build_engine_result(df: pd.DataFrame, df_clean: pd.DataFrame,
                          df_idx: pd.DataFrame, df_fut: pd.DataFrame,
                          df_full_history: "pd.DataFrame | None",
                          symbol: str, expiry: str, dte: int, lot_size: int,
                          n_strikes_each_side: int = 999,
                          velocity_window_minutes: int = 15,  # deprecated, unused — velocity is now always 5/15/30min, see get_oi_velocity
                          near_expiry: str = "",
                          far_expiry: str = "",
                          india_vix: float = 0.0,
                          india_vix_chg_pct: float = 0.0) -> EngineResult:
    """One computation pass. Call this once per refresh; every render_*.py
    function reads its inputs off the returned EngineResult instead of
    recomputing them."""

    spot = df_clean["Spot"].iloc[0] if "Spot" in df_clean.columns and not df_clean.empty else float(df_clean['StrikePrice'].iloc[0])
    strikes_all = df_clean["StrikePrice"].tolist()
    atm = min(strikes_all, key=lambda k: abs(k - spot))
    atm_idx = strikes_all.index(atm)
    strike_step = get_strike_step(strikes_all)

    atm_row = df_clean[df_clean["StrikePrice"] == atm]
    base_iv = (
        atm_row["CE_IV"].iloc[0] / 100.0
        if not atm_row.empty and atm_row["CE_IV"].iloc[0] > 0
        else DEFAULT_BASE_IV
    )

    # ── master table (the one and only call to build_master_table_nse) ────
    # oi_analysis1.py computes dte internally per-row (from each row's own
    # Expiry, with an intraday fraction on expiry day) — no longer takes a
    # dte argument here.
    master = build_master_table_nse(df, spot, lot_size=lot_size)

    atm_rows = (
        master[master["strike"] == atm]
        if master is not None and "strike" in master.columns
        else pd.DataFrame()
    )
    bias = (
        atm_rows["combined_view"].iloc[0]
        if master is not None and "combined_view" in master.columns and not atm_rows.empty
        else "Neutral"
    )

    # ── canonical ATM±N window (the one and only strike-window slice) ─────
    window = _atm_window(df_clean, atm, strike_step, n_strikes_each_side)

    # ── Greeks table (full chain — window is kept as a compatibility alias) ──
    greeks_table = _build_greeks_table(master, spot, base_iv, dte, lot_size)

    # ── ATM Greeks (lot-adjusted, for KPI strip / exec summary / risk) ────
    t_param = max(dte / 365.0, _MIN_T_YEARS)
    atm_delta = bs_delta(spot, atm, t_param, ANNUAL_RISK_FREE_RATE, base_iv, "C")
    atm_theta = bs_theta(spot, atm, t_param, ANNUAL_RISK_FREE_RATE, base_iv, "C")
    atm_gamma = bs_gamma(spot, atm, t_param, ANNUAL_RISK_FREE_RATE, base_iv) * lot_size * spot / 100.0
    atm_vega = bs_vega(spot, atm, t_param, ANNUAL_RISK_FREE_RATE, base_iv) * lot_size

    ce_premium = df_clean["CE_LTP"].iloc[atm_idx] if "CE_LTP" in df_clean.columns else 0.0
    pe_premium = df_clean["PE_LTP"].iloc[atm_idx] if "PE_LTP" in df_clean.columns else 0.0

    # ── PCR / OI-change-PCR / walls / max pain (each computed exactly once) ─
    total_ce_oi = df_clean["CE_OI"].fillna(0).sum()
    total_pe_oi = df_clean["PE_OI"].fillna(0).sum()
    total_pcr = compute_total_pcr(df_clean)
    oi_chg_pcr = (
        df_clean["PE_ChgOI"].sum() / df_clean["CE_ChgOI"].sum()
        if df_clean["CE_ChgOI"].sum() > 0 else 0.0
    )

    ce_wall = (df_clean.loc[df_clean["CE_OI"].fillna(0).idxmax(), "StrikePrice"]
               if total_ce_oi > 0 else atm)
    pe_wall = (df_clean.loc[df_clean["PE_OI"].fillna(0).idxmax(), "StrikePrice"]
               if total_pe_oi > 0 else atm)

    # BUG FIX: max_pain was previously hardcoded to `atm` in option_chain.py's
    # ctx_dict; now computed for real, once, here.
    max_pain = compute_max_pain(df_clean)
    max_pain_dist = abs(spot - max_pain)

    # ── futures basis ───────────────────────────────────────────────────
    futures_ltp = df_fut["LTP"].iloc[0] if df_fut is not None and not df_fut.empty and "LTP" in df_fut.columns else spot
    basis = futures_ltp - spot
    fut_signal = "Long Buildup" if basis > 0 else "Short Buildup"

    # ── spot change / day move ──────────────────────────────────────────
    idx_row = (df_idx[df_idx["Symbol"] == symbol]
               if df_idx is not None and "Symbol" in df_idx.columns else pd.DataFrame())
    if idx_row is not None and not idx_row.empty:
        day_change = idx_row["Change"].iloc[0]
        day_chg_pct = idx_row["% Change"].iloc[0]
        if day_change is None or (isinstance(day_change, float) and pd.isna(day_change)):
            prev_close = idx_row["Prev Close"].iloc[0]
            day_change = (spot - prev_close) if prev_close else 0.0
            day_chg_pct = (day_change / prev_close * 100.0) if prev_close else 0.0
        if day_chg_pct is None or (isinstance(day_chg_pct, float) and pd.isna(day_chg_pct)):
            day_chg_pct = (day_change / spot * 100.0) if spot else 0.0
    else:
        day_change, day_chg_pct = 0.0, 0.0

    # ── VIX regime ───────────────────────────────────────────────────────
    # india_vix is always caller-supplied now (market_api.get_unified_market_data(),
    # called once per tick in option_chain_json.py). The old df_idx["Symbol"]=="INDIA VIX"
    # fallback was removed 2026-07-04 — it never fired (df_idx never actually
    # contained an INDIA VIX row; equity-stock-indices and allIndices are
    # different NSE endpoints) and was masking a duplicate NSE request via
    # "AllIndices" in market_api.DEFAULT_INDICES. get_unified_market_data() is the
    # single source of truth for VIX; 15.0 remains the only fallback, used
    # if that call itself fails or returns 0.
    if india_vix is None or india_vix <= 0:
        india_vix = 15.0
    vix_regime = "Low" if india_vix < 13.0 else "High" if india_vix > 18.0 else "Normal"

    pcr_sentiment = ("Balanced Range" if 0.8 < total_pcr < 1.2
                      else "PE Dominant" if total_pcr >= 1.2
                      else "CE Dominant")
    real_picture = "Zonal Sideways" if abs(basis) < 20 else "Directional Trend Break"

    # ── OI velocity (5/15/30-min windows, from the accumulated parquet
    # history — see oi_analysis.get_oi_velocity) ───────────────────────────
    vel_df = get_oi_velocity(df_clean, symbol, expiry, windows=(5, 15, 30), lot_size=lot_size)

    # ── RECORD THIS TICK INTO THE VELOCITY HISTORY LOG ──
    # get_oi_velocity() above reads _HISTORY_MEM (oi_analysis.py's
    # in-memory, parquet-backed accumulating log) looking for a snapshot
    # old enough to diff against for each window. But nothing was ever
    # calling append_json_history() to grow that log tick-to-tick —
    # _record_oi_snapshot() (mTerminals_json.py) writes into a completely
    # separate, unrelated in-memory structure (_OI_SNAPSHOTS_MEM) that only
    # _compute_vol_changes() reads. So _HISTORY_MEM stayed frozen at
    # whatever was loaded from oi_history_log.parquet at process start (or
    # empty, on a fresh install) for the entire life of the process, and
    # vel_df above came back empty every single tick — which is what was
    # showing up as OI Velocity / trend bars never populating on the
    # frontend (Option Chain tab's tri-bar sparkline, dOI summary, Change
    # OI chart on oi-dashboard.js). Appending here, AFTER this tick's
    # vel_df has already been computed against the PRIOR history, means
    # this tick's own numbers can't leak into their own delta — they only
    # become available as "prior" state for future ticks, which is what
    # actually builds up real time-lookback history over the next 5/15/30
    # minutes of live ticks.
    _hist_cols = ["StrikePrice", "CE_OI", "PE_OI", "CE_LTP", "PE_LTP"]
    if all(c in df_clean.columns for c in _hist_cols):
        history_snapshot = df_clean[_hist_cols].copy()
        history_snapshot["Symbol"] = symbol
        history_snapshot["Expiry"] = expiry
        history_snapshot["snapshot_time"] = pd.Timestamp.now()
        append_json_history(history_snapshot)

    # ── strategies / scenario P&L / risk meters / smart money ──────────────
    # AFTER:
    iv_rank, hv30 = _compute_iv_rank_hv30(df_full_history, base_iv, atm)

    # Trap detection now runs BEFORE scoring (previously it only ran at the
    # very end, inline in the return statement below, which meant the score
    # for each strategy had no way to know a BULL_TRAP/BEAR_TRAP/SQUEEZE or
    # a poor trade_grade was active). Computed once here and reused.
    trap_result = _detect_traps(
        spot, atm, ce_wall, pe_wall, strike_step,
        total_pcr, base_iv, india_vix, vel_df,
    )

    strategies = _build_strategies(spot, atm, strike_step, dte, base_iv, lot_size,
                                    near_expiry=near_expiry, far_expiry=far_expiry)
    strategy_scores = _score_strategies(
        strategies, spot, atm, total_pcr, iv_rank, dte,
        bias=bias, trap_str=trap_result["trap_str"], trade_grade=trap_result["trade_grade"],
    )
    scenario_pnl = _build_scenario_pnl(spot, atm_delta, lot_size)
    risk_meters = _build_risk_meters(atm_delta, atm_gamma, base_iv, atm_theta, lot_size, dte, total_pcr)
    smart_money_top = _build_smart_money_top(df_clean)
    vol_oi_ratios = _build_vol_oi_ratios(df_clean)

    # ── OTM wing premiums for decision_engine.py's spread/condor/strangle
    # BUY legs — looked up at the exact same strikes those legs use
    # (atm ± 2*strike_step), from the same df_clean chain slice everything
    # else here reads.
    #
    # BUGFIX #2: live LTP alone still left this None for any strike that
    # simply hasn't traded yet today (normal for a far OTM wing, especially
    # early session) — a market-data gap, not a code bug, but it meant the
    # decision box kept failing with "LTP not fetched" in exactly the cases
    # _build_strategies() below never has a problem with, because that
    # function never touches live chain data for its own OTM legs (otm_c/
    # otm_p/wing_c/wing_p) — it always prices them off Black-Scholes. Apply
    # the same fallback here: real live LTP when the market has one, the
    # same bs_call/bs_put + get_iv_skew estimate _build_strategies() already
    # uses for its own far-strike legs otherwise. Real data is still
    # preferred when it exists; this only fills the gap, it doesn't
    # override a genuine live price.
    _t_param = max(dte / 365.0, _MIN_T_YEARS)
    _r_param = ANNUAL_RISK_FREE_RATE

    def _wing_ltp(strike: float, col: str) -> "float | None":
        if col in df_clean.columns:
            row = df_clean[df_clean["StrikePrice"] == strike]
            if not row.empty:
                val = row[col].iloc[0]
                if pd.notna(val) and val > 0:
                    return float(val)
        # No live price for this strike yet — theoretical fallback.
        skew_iv = get_iv_skew(strike, spot, base_iv)
        theo = bs_call(spot, strike, _t_param, _r_param, skew_iv) if col == "CE_LTP" \
            else bs_put(spot, strike, _t_param, _r_param, skew_iv)
        return round(theo, 2) if theo > 0 else None

    wing_premiums = {
        "pe_buy": _wing_ltp(atm - 2 * strike_step, "PE_LTP"),
        "ce_buy": _wing_ltp(atm + 2 * strike_step, "CE_LTP"),
    }

    return EngineResult(
        symbol=symbol, expiry=expiry, dte=dte, spot=spot, atm=atm,
        atm_idx=atm_idx, strike_step=strike_step, lot_size=lot_size, base_iv=base_iv,
        master=master, window=window, greeks_table=greeks_table, vel_df=vel_df,
        total_pcr=total_pcr, oi_chg_pcr=oi_chg_pcr,
        max_pain=max_pain, max_pain_dist=max_pain_dist,
        ce_wall=ce_wall, pe_wall=pe_wall,
        atm_delta=atm_delta, atm_theta=atm_theta, atm_gamma=atm_gamma, atm_vega=atm_vega,
        ce_premium=ce_premium, pe_premium=pe_premium,
        iv_rank=iv_rank, hv30=hv30, india_vix=india_vix, vix_regime=vix_regime,
        india_vix_chg_pct=india_vix_chg_pct,
        basis=basis, fut_signal=fut_signal, pcr_sentiment=pcr_sentiment, bias=bias,
        is_up=day_change >= 0, spot_change=day_change, spot_chg_pct=day_chg_pct,
        real_picture=real_picture, **trap_result,
        strategies=strategies, strategy_scores=strategy_scores,
        scenario_pnl=scenario_pnl, risk_meters=risk_meters,
        smart_money_top=smart_money_top,
        vol_oi_ratios=vol_oi_ratios,
        wing_premiums=wing_premiums,
        near_expiry=near_expiry, far_expiry=far_expiry,
        oi_history_snapshot=df_full_history,
    )