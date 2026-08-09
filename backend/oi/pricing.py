"""
oi/pricing.py
-------------
Black-Scholes pricing, Greeks, IV skew, and IV solver primitives.

Moved from engine.py (Step 4a of the v3 migration plan) — these functions
were originally merged into engine.py from a since-deleted greeks_engine.py.

RECONCILED (rate + q, this pass): two separate constant drifts were found
between this module and oi/oi_analysis.py, and only one is a straight
constant fix.

1. ANNUAL_RISK_FREE_RATE was 0.065 here vs. 0.07 in oi/oi_analysis.py and
   smartapi_pipeline_adapter.py's own default — kept at 0.07 (the value
   two of the three independent copies already agreed on). Every live
   call site imports this constant from here, so this one change
   propagates everywhere.

2. Dividend yield (q) is NOT a straight reconciliation — it's a real
   per-instrument product decision (NIFTY q=1.23%, BANKNIFTY q=0, see
   DIVIDEND_YIELD_BY_SYMBOL / get_dividend_yield() below). bs_delta/
   bs_gamma/bs_vega/bs_theta/bs_rho/bs_charm/bs_vanna all take an
   optional `q` (default 0.0, backward compatible); callers that need
   the right per-symbol value must call get_dividend_yield(symbol) and
   pass it explicitly — nothing here infers the symbol automatically.

This module is the single source of truth for Black-Scholes Greeks. Both
the live OI master-table pipeline and the compatibility OptionChainEngine
delegate batch calculations to bs_greeks_vectorized(), while point lookups
use the scalar bs_* functions below.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.special import ndtr

__all__ = [
    "bs_call", "bs_put",
    "bs_delta", "bs_gamma", "bs_vega", "bs_theta", "bs_rho",
    "bs_charm", "bs_vanna",
    "bs_greeks_vectorized",
    "get_iv_skew",
    "norm_pdf", "norm_cdf",
    "solve_iv",
    "get_atm_iv",
    "ANNUAL_RISK_FREE_RATE",
    "DIVIDEND_YIELD",
    "DIVIDEND_YIELD_BY_SYMBOL",
    "get_dividend_yield",
    "DEFAULT_BASE_IV",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ANNUAL_RISK_FREE_RATE = 0.07    # 7% — India repo/T-bill rate proxy (reconciled, see module docstring)
DIVIDEND_YIELD        = 0.0123  # approx NIFTY dividend yield — default q, kept for backward compat
                                 # with any call site not yet updated to pass q explicitly

# Per-instrument dividend yield. NIFTY-family broad-market indices use the
# ~1.23% approx NIFTY dividend yield; BANKNIFTY is q=0 since bank stocks
# pay little dividend and desks conventionally price it unadjusted (per
# product decision, 2026-08-01). FINNIFTY/MIDCPNIFTY/SENSEX are NOT yet
# confirmed with the desk — defaulted to the NIFTY value as the closer
# analogue (broad-market, not bank-heavy); revisit if that's wrong.
DIVIDEND_YIELD_BY_SYMBOL = {
    "NIFTY":      0.0123,
    "BANKNIFTY":  0.0,
    "FINNIFTY":   0.0123,   # assumption — not yet confirmed with desk
    "MIDCPNIFTY": 0.0123,   # assumption — not yet confirmed with desk
    "SENSEX":     0.0123,   # assumption — not yet confirmed with desk
    "SENSEX50":   0.0123,   # assumption — broad-market, treated like NIFTY
    "BANKEX":     0.0,      # assumption — bank index, treated like BANKNIFTY
}


def get_dividend_yield(symbol: str) -> float:
    """Per-instrument Black-Scholes q. Falls back to DIVIDEND_YIELD (the
    NIFTY value) for any symbol not in DIVIDEND_YIELD_BY_SYMBOL."""
    return DIVIDEND_YIELD_BY_SYMBOL.get(symbol.upper(), DIVIDEND_YIELD)
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

def _d1(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    return (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    return _d1(S, K, T, r, sigma, q) - sigma * math.sqrt(T)


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
# Greeks (dividend-yield aware — see module docstring, dedup-greeks)
# ---------------------------------------------------------------------------

def bs_delta(S: float, K: float, T: float, r: float, sigma: float,
             opt_type: str = "C", q: float = 0.0) -> float:
    """Black-Scholes Delta. opt_type: 'C' call / 'P' put."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        if opt_type.upper() == "C":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    exp_qt = math.exp(-q * T)
    if opt_type.upper() == "C":
        return exp_qt * norm_cdf(d1)
    return exp_qt * (norm_cdf(d1) - 1.0)


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Black-Scholes Gamma (same for calls and puts)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    return math.exp(-q * T) * norm_pdf(d1) / (S * sigma * math.sqrt(T))


def bs_vega(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Black-Scholes Vega — ₹ per 1% IV move (divided by 100)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    return S * math.exp(-q * T) * norm_pdf(d1) * math.sqrt(T) / 100.0


def bs_theta(S: float, K: float, T: float, r: float, sigma: float,
             opt_type: str = "C", q: float = 0.0) -> float:
    """Black-Scholes Theta — ₹/day (negative for long options)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    exp_qt, exp_rt = math.exp(-q * T), math.exp(-r * T)
    decay = -(S * exp_qt * norm_pdf(d1) * sigma) / (2.0 * math.sqrt(T))
    if opt_type.upper() == "C":
        return (decay - r * K * exp_rt * norm_cdf(d2) + q * S * exp_qt * norm_cdf(d1)) / 365.0
    return (decay + r * K * exp_rt * norm_cdf(-d2) - q * S * exp_qt * norm_cdf(-d1)) / 365.0


def bs_rho(S: float, K: float, T: float, r: float, sigma: float,
           opt_type: str = "C", q: float = 0.0) -> float:
    """Black-Scholes Rho — ₹ per 1% change in risk-free rate."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d2 = _d2(S, K, T, r, sigma, q)
    exp_rt = math.exp(-r * T)
    if opt_type.upper() == "C":
        return K * T * exp_rt * norm_cdf(d2) / 100.0
    return -K * T * exp_rt * norm_cdf(-d2) / 100.0


def bs_vanna(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """∂Delta/∂vol (equivalently ∂Vega/∂spot). Same for calls and puts."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    return -math.exp(-q * T) * norm_pdf(d1) * d2 / sigma


def bs_charm(S: float, K: float, T: float, r: float, sigma: float,
             opt_type: str = "C", q: float = 0.0) -> float:
    """∂Delta/∂time ("delta decay")."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    sqrt_t = math.sqrt(T)
    exp_qt = math.exp(-q * T)
    common = exp_qt * norm_pdf(d1) * ((2 * (r - q) * T - d2 * sigma * sqrt_t) / (2 * T * sigma * sqrt_t))
    if opt_type.upper() == "C":
        return -q * exp_qt * norm_cdf(d1) - common
    return q * exp_qt * norm_cdf(-d1) - common


def bs_greeks_vectorized(spot, strikes, t, r, q, sigma, option_type):
    """Vectorized batch Greeks over an entire strike column at once — the
    performance-critical path for oi/oi_analysis.py's build_master_table_nse()
    (~100-150 strikes recomputed per tick, per expiry chain). Same formulas
    as the scalar bs_* functions above, just computed as NumPy array ops
    instead of one Python call per row.

    Parameters
    ----------
    spot : float                    (single spot price for the whole chain)
    strikes, t, sigma : array-like  (per-row values, same length)
    r, q : float                    (risk-free rate, dividend yield)
    option_type : "CE" or "PE"

    Returns
    -------
    7 float64 ndarrays: delta, gamma, theta, vega, rho, charm, vanna
    (0.0 at any index where spot/strike/t/sigma was invalid, matching the
    scalar functions' early-return behavior).
    """
    strikes = np.asarray(strikes, dtype=np.float64)
    t       = np.asarray(t, dtype=np.float64)
    sigma   = np.asarray(sigma, dtype=np.float64)
    n = strikes.shape[0]

    delta = np.zeros(n); gamma = np.zeros(n); theta = np.zeros(n)
    vega  = np.zeros(n); rho   = np.zeros(n); charm = np.zeros(n); vanna = np.zeros(n)

    valid = (spot > 0) & (strikes > 0) & (t > 0) & (sigma > 0)
    if not np.any(valid):
        return delta, gamma, theta, vega, rho, charm, vanna

    K, T, sg = strikes[valid], t[valid], sigma[valid]
    sqrt_t = np.sqrt(T)
    d1 = (np.log(spot / K) + (r - q + 0.5 * sg ** 2) * T) / (sg * sqrt_t)
    d2 = d1 - sg * sqrt_t
    nd1, nd2 = ndtr(d1), ndtr(d2)
    pdf_d1 = np.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
    exp_qt = np.exp(-q * T)
    exp_rt = np.exp(-r * T)

    g = exp_qt * pdf_d1 / (spot * sg * sqrt_t)
    v = spot * exp_qt * pdf_d1 * sqrt_t / 100
    vn = -exp_qt * pdf_d1 * d2 / sg

    if option_type.upper() == "CE":
        d = exp_qt * nd1
        th = ((-spot * exp_qt * pdf_d1 * sg) / (2 * sqrt_t)
              - r * K * exp_rt * nd2 + q * spot * exp_qt * nd1) / 365
        rh = K * T * exp_rt * nd2 / 100
        ch = (-q * exp_qt * nd1 - exp_qt * pdf_d1 *
              ((2 * (r - q) * T - d2 * sg * sqrt_t) / (2 * T * sg * sqrt_t)))
    else:
        nd1n, nd2n = ndtr(-d1), ndtr(-d2)
        d = exp_qt * (nd1 - 1)
        th = ((-spot * exp_qt * pdf_d1 * sg) / (2 * sqrt_t)
              + r * K * exp_rt * nd2n - q * spot * exp_qt * nd1n) / 365
        rh = -K * T * exp_rt * nd2n / 100
        ch = (q * exp_qt * nd1n - exp_qt * pdf_d1 *
              ((2 * (r - q) * T - d2 * sg * sqrt_t) / (2 * T * sg * sqrt_t)))

    delta[valid], gamma[valid], theta[valid] = d, g, th
    vega[valid], rho[valid], charm[valid], vanna[valid] = v, rh, ch, vn
    return delta, gamma, theta, vega, rho, charm, vanna


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
