"""
oi_analysis.py (Unified, Optimized, & Expiry-Fraction Ready)
High-performance algorithmic profiling optimizing Black-Scholes Greeks throughput,
eliminating pandas iteration overhead, and utilizing fast dictionary mapping.
Fully unifies column names into standard snake_case without spaces.
"""
import math
import os
import numpy as np
import pandas as pd
from datetime import datetime
from scipy.special import ndtr

# ---------------------------------------------------------------
# Configurable constants
# ---------------------------------------------------------------
ANNUAL_RISK_FREE_RATE = 0.07     # India ~ repo/T-bill rate
DIVIDEND_YIELD = 0.0123          # approx NIFTY dividend yield
HIGH_VOLUME_THRESHOLD = 500000   # contracts
ATM_BAND = 50                    # +/- points from spot considered ATM

# Fast lookup math caches
SQRT_2 = math.sqrt(2)
SQRT_2_PI = math.sqrt(2 * math.pi)


# ---------------------------------------------------------------
# Black-Scholes Greeks Engine
# ---------------------------------------------------------------
def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / SQRT_2))


def _norm_pdf(x):
    return math.exp(-0.5 * x ** 2) / SQRT_2_PI


def calculate_greeks(spot, strike, t, r, q, sigma, option_type):
    """Returns (delta, gamma, theta, vega, rho, charm, vanna) for one option leg."""
    if spot <= 0 or strike <= 0 or t <= 0 or sigma <= 0:
        return (0, 0, 0, 0, 0, 0, 0)

    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma ** 2) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    nd1, nd2 = _norm_cdf(d1), _norm_cdf(d2)
    pdf_d1 = _norm_pdf(d1)
    exp_qt = math.exp(-q * t)
    exp_rt = math.exp(-r * t)

    gamma = exp_qt * pdf_d1 / (spot * sigma * sqrt_t)
    vega = spot * exp_qt * pdf_d1 * sqrt_t / 100
    vanna = -exp_qt * pdf_d1 * d2 / sigma

    if option_type.upper() == "CE":
        delta = exp_qt * nd1
        theta = ((-spot * exp_qt * pdf_d1 * sigma) / (2 * sqrt_t)
                 - r * strike * exp_rt * nd2 + q * spot * exp_qt * nd1) / 365
        rho = strike * t * exp_rt * nd2 / 100
        charm = (-q * exp_qt * nd1 - exp_qt * pdf_d1 *
                 ((2 * (r - q) * t - d2 * sigma * sqrt_t) / (2 * t * sigma * sqrt_t)))
    else:
        nd1n, nd2n = _norm_cdf(-d1), _norm_cdf(-d2)
        delta = exp_qt * (nd1 - 1)
        theta = ((-spot * exp_qt * pdf_d1 * sigma) / (2 * sqrt_t)
                 + r * strike * exp_rt * nd2n - q * spot * exp_qt * nd1n) / 365
        rho = -strike * t * exp_rt * nd2n / 100
        charm = (q * exp_qt * nd1n - exp_qt * pdf_d1 *
                 ((2 * (r - q) * t - d2 * sigma * sqrt_t) / (2 * t * sigma * sqrt_t)))

    return delta, gamma, theta, vega, rho, charm, vanna


def calculate_greeks_vectorized(spot, strikes, t, r, q, sigma, option_type):
    """
    Vectorized twin of calculate_greeks() — same Black-Scholes formulas,
    same edge-case behavior (0 wherever spot/strike/t/sigma are invalid),
    but computed as NumPy array ops over an ENTIRE strike column at once
    instead of one Python function call (with several math.exp/erf/log
    calls each) per row. This is the CPU-heavy part of
    build_master_table_nse() — with ~100-150 strikes recomputed for the
    main chain plus every extra expiry chain (NEAR/MONTHLY) each tick,
    the per-row Python loop was the dominant non-network cost per refresh.

    ndtr() is scipy's standard normal CDF — mathematically identical to
    the 0.5*(1+erf(x/sqrt(2))) formula used in the scalar version above
    (verified: ndtr(0.5) == 0.5*(1+math.erf(0.5/sqrt(2))) to full float
    precision), just vectorized in C rather than looped in Python.

    Parameters
    ----------
    spot : float                    (single spot price for the whole chain)
    strikes, t, sigma : array-like  (per-row values, same length)
    r, q : float                    (risk-free rate, dividend yield)
    option_type : "CE" or "PE"

    Returns
    -------
    7 float64 ndarrays: delta, gamma, theta, vega, rho, charm, vanna
    (0.0 at any index where spot/strike/t/sigma was invalid — matches
    calculate_greeks()'s early-return behavior exactly).
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
    pdf_d1  = np.exp(-0.5 * d1 ** 2) / SQRT_2_PI
    exp_qt  = np.exp(-q * T)
    exp_rt  = np.exp(-r * T)

    g  = exp_qt * pdf_d1 / (spot * sg * sqrt_t)
    v  = spot * exp_qt * pdf_d1 * sqrt_t / 100
    vn = -exp_qt * pdf_d1 * d2 / sg

    if option_type.upper() == "CE":
        d  = exp_qt * nd1
        th = ((-spot * exp_qt * pdf_d1 * sg) / (2 * sqrt_t)
              - r * K * exp_rt * nd2 + q * spot * exp_qt * nd1) / 365
        rh = K * T * exp_rt * nd2 / 100
        ch = (-q * exp_qt * nd1 - exp_qt * pdf_d1 *
              ((2 * (r - q) * T - d2 * sg * sqrt_t) / (2 * T * sg * sqrt_t)))
    else:
        nd1n, nd2n = ndtr(-d1), ndtr(-d2)
        d  = exp_qt * (nd1 - 1)
        th = ((-spot * exp_qt * pdf_d1 * sg) / (2 * sqrt_t)
              + r * K * exp_rt * nd2n - q * spot * exp_qt * nd1n) / 365
        rh = -K * T * exp_rt * nd2n / 100
        ch = (q * exp_qt * nd1n - exp_qt * pdf_d1 *
              ((2 * (r - q) * T - d2 * sg * sqrt_t) / (2 * T * sg * sqrt_t)))

    delta[valid] = d;  gamma[valid] = g;  theta[valid] = th
    vega[valid]  = v;  rho[valid]   = rh
    charm[valid] = ch; vanna[valid] = vn

    return delta, gamma, theta, vega, rho, charm, vanna


# ---------------------------------------------------------------
# Signal Classification Matrix
# ---------------------------------------------------------------
def classify_buildup(price_chg, oi_chg):
    if price_chg > 0 and oi_chg > 0:
        return "Buying BuildUp"
    if price_chg < 0 and oi_chg > 0:
        return "Writing BuildUp"
    if price_chg > 0 and oi_chg < 0:
        return "Short Covering"
    if price_chg < 0 and oi_chg < 0:
        return "Long Unwinding"
    return "Neutral"


def signal_strength(price_chg, oi_pct, vol, is_atm, high_volume_threshold=HIGH_VOLUME_THRESHOLD):
    score = 0
    if abs(price_chg) > 5:
        score += 2
    if abs(oi_pct) > 10:
        score += 3
    if vol > high_volume_threshold:
        score += 3
    if is_atm:
        score += 2
    return score


def combined_view(ce_signal, pe_signal, ce_strength, pe_strength):
    total = ce_strength + pe_strength
    if ce_signal == "Short Covering" and pe_signal == "Writing BuildUp" and total >= 8:
        return "Strong Bullish"
    if "Writing" in pe_signal or ce_signal == "Short Covering":
        return "Bullish"
    if "Writing" in ce_signal and pe_signal == "Short Covering" and total >= 8:
        return "Strong Bearish"
    if "Writing" in ce_signal or pe_signal == "Short Covering":
        return "Bearish"
    if "Buying" in ce_signal and "Buying" in pe_signal:
        return "Range Bound"
    return "Mixed / Neutral"


def moneyness_label(spot, strike, atm_band=ATM_BAND):
    if abs(strike - spot) <= atm_band:
        return "ATM"
    return "ITM" if strike < spot else "OTM"


def compute_dte(expiry_date, today=None):
    """
    🎯 UPGRADED: High-Resolution Intraday Fraction Tracker
    Calculates precise minutes remaining on Expiry day to prevent 0-division bugs.
    """
    now = datetime.now()
    # Fast path: every caller in this pipeline (market_api.py,
    # option_chain_json.py, expiry_manager.py) always hands this a
    # "DD-Mon-YYYY" string (e.g. "04-Jul-2026"). Profiling
    # build_master_table_nse showed pd.to_datetime()'s regex-based format
    # auto-detection was ~85% of that function's ENTIRE runtime — it was
    # being called once per strike row (100-150x per chain, every tick),
    # re-guessing the identical format every single time. A plain
    # datetime.strptime() with the known format is orders of magnitude
    # faster and returns the same result. Falls back to pandas' general
    # parser for anything that isn't a plain "DD-Mon-YYYY" string, so this
    # stays robust to datetime/Timestamp objects or other formats.
    if isinstance(expiry_date, str):
        try:
            expiry = datetime.strptime(expiry_date, "%d-%b-%Y").date()
        except ValueError:
            expiry = pd.to_datetime(expiry_date).date()
    else:
        expiry = pd.to_datetime(expiry_date).date()
    if today is None:
        today = now.date()

    days_remaining = (expiry - today).days
    
    if days_remaining > 0:
        return float(days_remaining)
        
    if days_remaining == 0:
        # Check active remaining minutes until the 3:30 PM IST closing bell
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        minutes_left = (market_close - now).total_seconds() / 60.0
        
        if minutes_left <= 0:
            return 1e-6 # Protection floor
        return minutes_left / 1440.0 # Standardize minutes to day decimal fractions
        
    return 1e-6


def oi_pct_change(oi, oi_chg):
    prev_oi = oi - oi_chg
    return (oi_chg / prev_oi) * 100 if prev_oi > 0 else 0


# ---------------------------------------------------------------
# ⚡ UNIFIED: Standardized Flat Master Table
# ---------------------------------------------------------------
def build_master_table_nse(df, spot, risk_free_rate=ANNUAL_RISK_FREE_RATE,
                            dividend_yield=DIVIDEND_YIELD,
                            high_volume_threshold=HIGH_VOLUME_THRESHOLD,
                            atm_band=ATM_BAND, lot_size=1):
    """
    Processes records instantly via dictionary mapping. 
    Outputs clean snake_case variables to unify schemas.

    NSE's CE_OI/PE_OI/CE_ChgOI/PE_ChgOI are contract (lot) counts, not
    underlying quantity. `lot_size` scales ce_oi/pe_oi/ce_oi_chg/pe_oi_chg
    (and everything derived from them: net_oi, net_oi_chg, ce_oi_share,
    total_ce_oi) up to quantity terms. Defaults to 1 (raw contracts) so any
    caller that doesn't pass it keeps the old behavior.
    """
    total_ce_oi = df['CE_OI'].sum() * lot_size
    records = df.to_dict(orient='records')
    rows = []

    # ── Vectorized Greeks pre-pass ──────────────────────────────────────
    # Previously each row called calculate_greeks() individually (up to
    # ~150 Python calls per chain, each doing several math.exp/erf/log
    # calls) — the dominant CPU cost of this function, paid again for
    # every extra expiry chain (NEAR/MONTHLY) every tick. Instead, pull
    # the per-row inputs into flat arrays once, run the whole column
    # through calculate_greeks_vectorized() in one shot per side (CE/PE),
    # then have the per-row loop below just index into the results.
    # Validity conditions (iv > 0 and oi > 0) are preserved exactly —
    # output is identical to the old row-by-row version, just computed
    # as NumPy array ops instead of a Python loop.
    # Almost every row in a chain shares the same expiry string (it's a
    # per-expiry chain), so compute_dte() only needs to run once per
    # UNIQUE expiry value, not once per row — a 100-150x reduction in
    # calls for the typical single-expiry chain, on top of the strptime
    # fast-path fix in compute_dte() itself.
    _dte_cache: dict = {}
    def _dte_years(exp_str):
        if exp_str not in _dte_cache:
            _dte_cache[exp_str] = compute_dte(exp_str) / 365.0
        return _dte_cache[exp_str]
    t_arr = np.array([_dte_years(r['Expiry']) for r in records], dtype=np.float64)
    strikes_arr = np.array([r['StrikePrice'] for r in records], dtype=np.float64)
    ce_iv_arr = np.array([r['CE_IV'] or 0 for r in records], dtype=np.float64)
    pe_iv_arr = np.array([r['PE_IV'] or 0 for r in records], dtype=np.float64)
    ce_oi_arr = np.array([r['CE_OI'] for r in records], dtype=np.float64)
    pe_oi_arr = np.array([r['PE_OI'] for r in records], dtype=np.float64)

    ce_valid = (ce_iv_arr > 0) & (ce_oi_arr > 0)
    pe_valid = (pe_iv_arr > 0) & (pe_oi_arr > 0)

    (ce_delta_arr, ce_gamma_arr, ce_theta_arr, ce_vega_arr,
     ce_rho_arr, ce_charm_arr, ce_vanna_arr) = calculate_greeks_vectorized(
        spot, strikes_arr, t_arr, risk_free_rate, dividend_yield, ce_iv_arr / 100.0, "CE")
    (pe_delta_arr, pe_gamma_arr, pe_theta_arr, pe_vega_arr,
     pe_rho_arr, pe_charm_arr, pe_vanna_arr) = calculate_greeks_vectorized(
        spot, strikes_arr, t_arr, risk_free_rate, dividend_yield, pe_iv_arr / 100.0, "PE")

    # calculate_greeks_vectorized only zeroes out spot/strike/t/sigma
    # invalidity — the original also required ce_oi/pe_oi > 0 before even
    # attempting the calculation, so enforce that here too.
    for arr in (ce_delta_arr, ce_gamma_arr, ce_theta_arr, ce_vega_arr,
                ce_rho_arr, ce_charm_arr, ce_vanna_arr):
        arr[~ce_valid] = 0.0
    for arr in (pe_delta_arr, pe_gamma_arr, pe_theta_arr, pe_vega_arr,
                pe_rho_arr, pe_charm_arr, pe_vanna_arr):
        arr[~pe_valid] = 0.0

    for i, r in enumerate(records):
        strike = r['StrikePrice']
        expiry_str = r['Expiry']

        t = t_arr[i]

        ce_oi, pe_oi = r['CE_OI'] * lot_size, r['PE_OI'] * lot_size
        ce_oi_chg, pe_oi_chg = r['CE_ChgOI'] * lot_size, r['PE_ChgOI'] * lot_size
        
        ce_pct = oi_pct_change(ce_oi, ce_oi_chg)
        pe_pct = oi_pct_change(pe_oi, pe_oi_chg)
        
        ce_vol, pe_vol = r['CE_Volume'], r['PE_Volume']
        ce_ltp, pe_ltp = r['CE_LTP'], r['PE_LTP']

        ce_ltp_chg = r.get('CE_Change') or 0
        pe_ltp_chg = r.get('PE_Change') or 0
        ce_iv, pe_iv = ce_iv_arr[i], pe_iv_arr[i]

        is_atm = abs(strike - spot) <= atm_band
        money = moneyness_label(spot, strike, atm_band)

        # Greeks already computed for the whole column in the vectorized
        # pre-pass above — just index in, no per-row math here anymore.
        ce_delta, ce_gamma, ce_theta = ce_delta_arr[i], ce_gamma_arr[i], ce_theta_arr[i]
        ce_vega, ce_rho, ce_charm, ce_vanna = ce_vega_arr[i], ce_rho_arr[i], ce_charm_arr[i], ce_vanna_arr[i]
        pe_delta, pe_gamma, pe_theta = pe_delta_arr[i], pe_gamma_arr[i], pe_theta_arr[i]
        pe_vega, pe_rho, pe_charm, pe_vanna = pe_vega_arr[i], pe_rho_arr[i], pe_charm_arr[i], pe_vanna_arr[i]

        ce_signal = classify_buildup(ce_ltp_chg, ce_oi_chg)
        pe_signal = classify_buildup(pe_ltp_chg, pe_oi_chg)
        ce_strength = signal_strength(ce_ltp_chg, ce_pct, ce_vol, is_atm, high_volume_threshold)
        pe_strength = signal_strength(pe_ltp_chg, pe_pct, pe_vol, is_atm, high_volume_threshold)
        combined = combined_view(ce_signal, pe_signal, ce_strength, pe_strength)

        # 🎯 Standardized Snake_Case Headers
        rows.append({
            'strike': strike, 'expiry': expiry_str, 'moneyness': money,
            'ce_oi': ce_oi, 'ce_oi_chg': ce_oi_chg, 'ce_oi_pct': ce_pct,
            'ce_volume': ce_vol, 'ce_ltp': ce_ltp, 'ce_ltp_chg': ce_ltp_chg,
            'ce_signal': ce_signal, 'ce_strength': ce_strength,
            'pe_oi': pe_oi, 'pe_oi_chg': pe_oi_chg, 'pe_oi_pct': pe_pct,
            'pe_volume': pe_vol, 'pe_ltp': pe_ltp, 'pe_ltp_chg': pe_ltp_chg,
            'pe_signal': pe_signal, 'pe_strength': pe_strength,
            'combined_view': combined,
            'pcr_oi': round(pe_oi / ce_oi, 2) if ce_oi > 0 else 0,
            'pcr_vol': round(pe_vol / ce_vol, 2) if ce_vol > 0 else 0,
            # PCR of the day's fresh OI build (PE_ChgOI / CE_ChgOI), not the
            # change in PCR itself. ce_oi_chg == 0 -> undefined (no fresh call
            # activity to divide by); ce_oi_chg < 0 -> call OI unwound today,
            # ratio sign flips and should be read as "unwinding", not a
            # normal bullish/bearish PCR level.
            'pcr_oi_chg': (
                round(pe_oi_chg / ce_oi_chg, 2) if ce_oi_chg != 0 else None
            ),
            'net_oi': pe_oi - ce_oi, 'net_oi_chg': pe_oi_chg - ce_oi_chg,
            'ce_oi_share': (ce_oi / total_ce_oi) if total_ce_oi > 0 else 0,
            'ce_iv': ce_iv, 'ce_delta': ce_delta, 'ce_gamma': ce_gamma, 'ce_theta': ce_theta,
            'ce_vega': ce_vega, 'ce_rho': ce_rho, 'ce_charm': ce_charm, 'ce_vanna': ce_vanna,
            'pe_iv': pe_iv, 'pe_delta': pe_delta, 'pe_gamma': pe_gamma, 'pe_theta': pe_theta,
            'pe_vega': pe_vega, 'pe_rho': pe_rho, 'pe_charm': pe_charm, 'pe_vanna': pe_vanna,
            'iv_skew': ce_iv - pe_iv, 'sr_tag': ''
        })

    master = pd.DataFrame(rows)

    for rank, idx in enumerate(master.nlargest(3, 'ce_oi').index, start=1):
        master.at[idx, 'sr_tag'] = f"R{rank}"
    for rank, idx in enumerate(master.nlargest(3, 'pe_oi').index, start=1):
        master.at[idx, 'sr_tag'] = f"S{rank}"

    return master


# ---------------------------------------------------------------
# ⚡ Delta Log Storage Module
# ---------------------------------------------------------------
def build_oi_history(df, symbol, prev_poll=None):
    prev_lookup = {}
    if prev_poll is not None and not prev_poll.empty:
        prev_lookup = {
            (symbol, pr['StrikePrice'], pr['Expiry']): {
                'CE_OI': pr.get('CE_OI', 0), 'CE_LTP': pr.get('CE_LTP', 0),
                'CE_Volume': pr.get('CE_Volume', 0), 'CE_IV': pr.get('CE_IV', 0),
                'PE_OI': pr.get('PE_OI', 0), 'PE_LTP': pr.get('PE_LTP', 0),
                'PE_Volume': pr.get('PE_Volume', 0), 'PE_IV': pr.get('PE_IV', 0),
            }
            for _, pr in prev_poll.iterrows()
        }

    rows = []
    now = pd.Timestamp.now()
    records = df.to_dict(orient='records')

    for r in records:
        strike, expiry = r['StrikePrice'], r['Expiry']
        prev = prev_lookup.get((symbol, strike, expiry))

        ce_oi, pe_oi = r.get('CE_OI', 0), r.get('PE_OI', 0)
        ce_ltp, pe_ltp = r.get('CE_LTP', 0), r.get('PE_LTP', 0)
        ce_vol, pe_vol = r.get('CE_Volume', 0), r.get('PE_Volume', 0)
        ce_iv, pe_iv = r.get('CE_IV', 0), r.get('PE_IV', 0)

        if prev is None:
            ce_oi_delta = pe_oi_delta = 0
            ce_ltp_delta = pe_ltp_delta = 0
            ce_vol_delta = pe_vol_delta = 0
            ce_iv_delta = pe_iv_delta = 0
        else:
            ce_oi_delta = ce_oi - prev['CE_OI']
            pe_oi_delta = pe_oi - prev['PE_OI']
            ce_ltp_delta = ce_ltp - prev['CE_LTP']
            pe_ltp_delta = pe_ltp - prev['PE_LTP']
            ce_vol_delta = ce_vol - prev['CE_Volume']
            pe_vol_delta = pe_vol - prev['PE_Volume']
            ce_iv_delta = ce_iv - prev['CE_IV']
            pe_iv_delta = pe_iv - prev['PE_IV']

        rows.append({
            'snapshot_time': now, 'Symbol': symbol, 'StrikePrice': strike, 'Expiry': expiry,
            'CE_OI': ce_oi, 'CE_OI_Delta': ce_oi_delta,
            'CE_LTP': ce_ltp, 'CE_LTP_Delta': ce_ltp_delta,
            'CE_Volume': ce_vol, 'CE_Volume_Delta': ce_vol_delta,
            'CE_IV': ce_iv, 'CE_IV_Delta': ce_iv_delta,
            'PE_OI': pe_oi, 'PE_OI_Delta': pe_oi_delta,
            'PE_LTP': pe_ltp, 'PE_LTP_Delta': pe_ltp_delta,
            'PE_Volume': pe_vol, 'PE_Volume_Delta': pe_vol_delta,
            'PE_IV': pe_iv, 'PE_IV_Delta': pe_iv_delta,
        })

    return pd.DataFrame(rows)


DEFAULT_STRIKE_STEP = 50

def get_strike_step(strikes, default=DEFAULT_STRIKE_STEP):
    uniq = sorted(set(round(float(s), 2) for s in strikes if s is not None))
    if len(uniq) < 2:
        return default
    diffs = [round(b - a, 2) for a, b in zip(uniq[:-1], uniq[1:])]
    return max(set(diffs), key=diffs.count)


# ---------------------------------------------------------------
# Parquet Storage Handlers
# ---------------------------------------------------------------
# read_last_json_snapshot() and append_json_history() used to each do
# their own full pd.read_parquet(log_path) on EVERY poll tick (5s
# cadence), and append_json_history() rewrote the ENTIRE file back to
# disk every tick too — 2 full reads + 1 full write per tick, with cost
# growing linearly as the 45-day retention window fills up. Over a
# trading session at 5s intervals across dozens of strikes this file
# grows into the millions of rows, and parquet has no incremental-append
# mode, so every write serializes the whole thing. Left alone, this was
# on track to become the new compute_dte()/iterrows() bottleneck.
#
# Fix: since ws_server_live.py runs this pipeline from one long-lived
# process (not re-spawned per tick), we keep the accumulating history as
# an in-memory DataFrame — loaded from disk once, then read/appended to
# in memory on every tick — and only flush back to disk periodically
# (time-based), not on every single tick. Same durability model already
# used for oi_snapshots.json (see _OI_SNAPSHOTS_MEM in mTerminals_json.py).
# A short-lived crash can lose at most _FLUSH_INTERVAL_SECONDS of history,
# which is an acceptable trade for removing per-tick disk I/O entirely.
JSON_HISTORY_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "oi_history_log.parquet"
)

_FLUSH_INTERVAL_SECONDS = 60  # write to disk at most once per minute

_HISTORY_MEM = {
    "df": None,            # in-memory accumulating DataFrame (None = not loaded yet)
    "log_path": None,      # path it was loaded from, so a different log_path re-loads
    "last_flush": None,    # datetime of last successful disk write
    "dirty": False,        # True if in-memory df has changes not yet flushed
}


def _ensure_history_loaded(log_path):
    """Load the on-disk parquet into memory once per process (or once per
    distinct log_path, mainly useful for tests). No-op on subsequent calls."""
    if _HISTORY_MEM["df"] is not None and _HISTORY_MEM["log_path"] == log_path:
        return

    df = pd.DataFrame()
    if os.path.exists(log_path):
        try:
            df = pd.read_parquet(log_path)
        except Exception as e:
            print(f"[JSON History] Couldn't read {log_path} ({e}). Starting from an empty in-memory log.")
            df = pd.DataFrame()

    _HISTORY_MEM["df"] = df
    _HISTORY_MEM["log_path"] = log_path
    _HISTORY_MEM["last_flush"] = datetime.now()
    _HISTORY_MEM["dirty"] = False


def read_last_json_snapshot(symbol, log_path=JSON_HISTORY_LOG_PATH):
    _ensure_history_loaded(log_path)
    existing = _HISTORY_MEM["df"]

    if existing is None or existing.empty or "snapshot_time" not in existing.columns:
        return None

    existing = existing[existing["Symbol"] == symbol]
    if existing.empty:
        return None

    last_ts = existing["snapshot_time"].max()
    prev_poll = existing[existing["snapshot_time"] == last_ts].copy()
    print(f"[JSON History] Found previous snapshot from {last_ts} ({len(prev_poll)} rows) for {symbol} — using it for deltas.")
    return prev_poll


def append_json_history(history_df, log_path=JSON_HISTORY_LOG_PATH, max_age_days=45,
                         flush_interval_seconds=_FLUSH_INTERVAL_SECONDS):
    """Appends new rows to the in-memory history and periodically flushes
    to disk (at most once every `flush_interval_seconds`), instead of
    reading + rewriting the whole parquet file on every call."""
    if history_df is None or history_df.empty:
        return

    _ensure_history_loaded(log_path)
    existing = _HISTORY_MEM["df"]
    combined = pd.concat([existing, history_df], ignore_index=True) if not existing.empty else history_df

    cutoff = pd.Timestamp.now() - pd.Timedelta(days=max_age_days)
    combined = combined[combined["snapshot_time"] >= cutoff]

    _HISTORY_MEM["df"] = combined
    _HISTORY_MEM["dirty"] = True

    now = datetime.now()
    due = (
        _HISTORY_MEM["last_flush"] is None
        or (now - _HISTORY_MEM["last_flush"]).total_seconds() >= flush_interval_seconds
    )
    if due:
        flush_history_to_disk(log_path)


def flush_history_to_disk(log_path=JSON_HISTORY_LOG_PATH):
    """Force-write the in-memory history to disk now, regardless of the
    flush interval. Call this from a graceful-shutdown path (e.g.
    ws_server_live.py's `finally` block) so process exit doesn't lose up
    to flush_interval_seconds of unflushed history. Safe to call even if
    nothing has changed (no-ops when not dirty)."""
    if _HISTORY_MEM["df"] is None or not _HISTORY_MEM["dirty"]:
        return
    try:
        _HISTORY_MEM["df"].to_parquet(log_path, index=False)
        _HISTORY_MEM["last_flush"] = datetime.now()
        _HISTORY_MEM["dirty"] = False
    except Exception as e:
        print(f"[JSON History] Could not write {log_path} ({e}). Snapshot was not persisted.")


# ---------------------------------------------------------------
# OI Velocity (replaces oi_velocity.py — scrapped)
# ---------------------------------------------------------------
# The old get_oi_velocity() lived in oi_velocity.py and took a
# `df_full_history` argument that, in practice, only ever carried a
# single tick's snapshot (option_chain_json.py built it fresh every poll
# via build_oi_history() and passed that straight through) — so a 5, 15,
# or 30-minute-old row could never exist in it, and vel_df came back
# empty every tick, for every window. This version reads straight off
# _HISTORY_MEM, the same parquet-backed log append_json_history() has
# been accumulating tick-by-tick all along, so real multi-timestamp
# history is actually available for the lookback.
def get_oi_velocity(current_df, symbol, expiry, windows=(5, 15, 30), lot_size=1,
                     log_path=JSON_HISTORY_LOG_PATH):
    """
    current_df: live df_clean-style chain (StrikePrice/CE_OI/PE_OI/CE_LTP/
        PE_LTP/Expiry columns) for `symbol`/`expiry`, right now.
    windows: lookback windows in minutes.
    lot_size: scales OI from raw NSE contract counts to underlying
        quantity, matching build_master_table_nse's convention.

    Returns a single DataFrame (one row per strike per window) with a
    'window' column, so callers filter per-window via
    vel_df[vel_df["window"] == w] — same shape mTerminals_json.py already
    expects. Empty DataFrame if no history old enough exists yet for any
    window (e.g. right after a process restart).

    Filters _HISTORY_MEM by BOTH symbol and expiry — the history log
    accumulates rows from every expiry chain built each tick (primary +
    NEAR + MONTHLY, built concurrently), all under the same Symbol.
    Filtering by symbol alone lets the per-strike lookup silently pick up
    a different expiry's OI at the same strike number (weekly vs monthly
    OI at the same strike can differ 10-100x), which is what was producing
    wildly-swinging, nonsense delta values.
    """
    _ensure_history_loaded(log_path)
    hist = _HISTORY_MEM["df"]
    if hist is None or hist.empty or "Symbol" not in hist.columns:
        return pd.DataFrame()

    hist = hist[(hist["Symbol"] == symbol) & (hist["Expiry"] == expiry)]
    if hist.empty or "snapshot_time" not in hist.columns:
        return pd.DataFrame()

    now_ts = pd.Timestamp.now()
    snap_times = hist["snapshot_time"].unique()
    current_by_strike = {r["StrikePrice"]: r for r in current_df.to_dict("records")}

    rows = []
    for w in windows:
        target_age = w * 60
        best_ts, best_diff = None, float("inf")
        for ts in snap_times:
            age = (now_ts - pd.Timestamp(ts)).total_seconds()
            diff = abs(age - target_age)
            if diff < best_diff and age >= target_age * 0.5:
                best_diff = diff
                best_ts = ts
        if best_ts is None:
            continue  # no snapshot old enough yet for this window

        # Scale factor to normalize the raw delta (measured over whatever
        # the ACTUAL elapsed time to best_ts was, which can legitimately
        # range from 0.5x to >1x of target_age given the matching logic
        # above) up to a consistent target_age-equivalent rate. Without
        # this, two consecutive ticks matching snapshots of different real
        # ages (e.g. 2.6min vs 4.9min for a nominal 5min window) produce
        # wildly different-looking deltas for the same underlying rate of
        # change — this is what was showing up as "abrupt" VEL jumps.
        actual_age = (now_ts - pd.Timestamp(best_ts)).total_seconds()
        scale = (target_age / actual_age) if actual_age > 0 else 1.0

        prev_by_strike = {
            r["StrikePrice"]: r
            for r in hist[hist["snapshot_time"] == best_ts].to_dict("records")
        }

        for strike, cur in current_by_strike.items():
            prev = prev_by_strike.get(strike)
            if prev is None:
                continue

            ce_now = float(cur.get("CE_OI", 0) or 0) * lot_size
            pe_now = float(cur.get("PE_OI", 0) or 0) * lot_size
            ce_doi = (ce_now - float(prev.get("CE_OI", 0) or 0) * lot_size) * scale
            pe_doi = (pe_now - float(prev.get("PE_OI", 0) or 0) * lot_size) * scale

            if ce_now == 0 and pe_now == 0:
                continue

            if ce_doi > 0 and pe_doi > 0:
                signal = "Writing BuildUp"
            elif ce_doi < 0 and pe_doi < 0:
                signal = "Unwinding"
            elif ce_doi > 0 and pe_doi <= 0:
                signal = "CE Build / PE Unwind"
            elif ce_doi <= 0 and pe_doi > 0:
                signal = "PE Build / CE Unwind"
            else:
                signal = "Neutral"

            rows.append({
                "window": w,
                "strike": int(strike),
                "ceNow": ce_now, "ceDOI": ce_doi,
                "ceLTP": round(float(cur.get("CE_LTP", 0) or 0), 1),
                "peNow": pe_now, "peDOI": pe_doi,
                "peLTP": round(float(cur.get("PE_LTP", 0) or 0), 1),
                "signal": signal,
                "actual_age_min": round(actual_age / 60.0, 2),
            })

    return pd.DataFrame(rows)
