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
  should import this canonical `decision.engine` module.
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

import numpy as np
import pandas as pd
from analytics.market_regime import classify_market_regime

# Trap detector — moved to decision/signal_builder.py as the public
# detect_traps() (previously decision/decision_engine.py's private
# _detect_traps; decision_engine.py still re-exports the old name for
# any other caller, but this one's updated to the real location).
from decision.signal_builder import detect_traps as _detect_traps
from decision.types import T

# Capital-weighted per-strike metrics (notional, premium locked, capital
# flow, delta/gamma exposure) — computed once off `master` here so every
# consumer reads the same numbers instead of re-deriving them.
from oi.capital_metrics import compute_capital_metrics

# Chain-level metrics — moved to oi/chain_metrics.py (Step 4a).
from oi.chain_metrics import (
    _atm_window,
    _build_greeks_table,
    _build_smart_money_top,
    _build_vol_oi_ratios,
    _compute_iv_rank_hv30,
    _summarize_gex,
    compute_max_pain,
    compute_total_pcr,
)
from oi.futures_oi_tracker import get_tracker as _get_futures_oi_tracker
from oi.oi_analysis import build_master_table_nse, get_oi_velocity, get_strike_step

# Black-Scholes pricing/Greeks primitives — moved to oi/pricing.py (Step 4a
# of the v3 migration plan). engine.py keeps only what OptionChainEngine,
# _detect_traps, and build_engine_result still need directly; solve_iv,
# get_atm_iv, bs_rho, norm_pdf/norm_cdf are used by nothing else in this
# file (confirmed via grep before the move) and are NOT re-imported here —
# consumers reach them via `from oi.pricing import ...` directly.
from oi.pricing import (
    _IV_SKEW_FLOOR,
    _MIN_T_YEARS,
    ANNUAL_RISK_FREE_RATE,
    DEFAULT_BASE_IV,
    bs_call,
    bs_delta,
    bs_gamma,
    bs_greeks_vectorized,
    bs_put,
    bs_theta,
    bs_vega,
    get_dividend_yield,
    get_iv_skew,
)

# Risk meters — moved to risk/risk_meters.py (Step 4c).
from risk.risk_meters import _build_risk_meters

# Strategy definitions, rule-based scoring, and scenario P&L — moved to
# strategy/strategies.py (Step 4b). build_engine_result() calls these three
# directly; _STRATEGY_DIRECTION/_STRATEGY_MAX_SCORE are internal to
# _score_strategies and are not used anywhere else in engine.py, so they
# stay in strategy/strategies.py and are not re-imported here.
from strategy.strategies import (
    _build_scenario_pnl,
    _build_strategies,
    _score_strategies,
)

__all__ = [
    "OptionChainEngine",
    "EngineResult",
    "build_engine_result",
]


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

        Vectorized over the whole chain through oi.pricing's canonical
        batch calculator. This used to carry an independent copy of the
        formulas, which could drift from the live OI pipeline.
        The scalar bs_* functions are untouched and still used for
        single-point lookups (atm_greeks(), solve_iv(), scenario P&L).
        """
        if chain is None or chain.empty:
            return chain

        S, T, r = self.spot, self.t, self.risk_free
        K = pd.to_numeric(chain[strike_col], errors="coerce").to_numpy(dtype=float)

        # Same skew curve as get_iv_skew(), vectorized: iv = max(base_iv + slope*(K-S), floor)
        iv = np.maximum(self.base_iv + self.skew_slope * (K - S), _IV_SKEW_FLOOR)

        t = np.full(K.shape, T, dtype=float)
        ce_delta, gamma, ce_theta, vega, *_ = bs_greeks_vectorized(
            S, K, t, r, 0.0, iv, "CE"
        )
        pe_delta, _, pe_theta, _, *_ = bs_greeks_vectorized(
            S, K, t, r, 0.0, iv, "PE"
        )

        # Preserve this public compatibility class's historical intrinsic
        # delta fallback for invalid strikes. The canonical live batch path
        # deliberately returns zero for all invalid inputs.
        invalid = (S <= 0) | (K <= 0)
        ce_delta = np.where(invalid, np.where(S > K, 1.0, 0.0), ce_delta)
        pe_delta = np.where(invalid, np.where(S < K, -1.0, 0.0), pe_delta)

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
# Strategy definitions/scoring + scenario P&L — moved to strategy/strategies.py
# (Step 4b of the v4 migration plan; see the import near the top of this
# file). engine.py calls into them below in build_engine_result(); nothing
# else in this file references _build_strategies/_score_strategies/
# _build_scenario_pnl or the _STRATEGY_DIRECTION/_STRATEGY_MAX_SCORE tables
# directly, so those two dicts are not re-imported here.
# ===========================================================================

# ===========================================================================
# Risk meters — moved to risk/risk_meters.py (Step 4c of the v4 migration
# plan; see the import near the top of this file).
# ===========================================================================

# ===========================================================================
# Trap detector — moved to decision/decision_engine.py (Step 4d of the v4
# migration plan; see the import near the top of this file). v3 had
# incorrectly claimed this was "already moved in Step 3" — it was not,
# until now.
# ===========================================================================

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
    # This is the real feature source for ml/features.py — do
    # not reconstruct these deltas from vel_df or ctx_dict elsewhere.
    oi_history_snapshot: "pd.DataFrame | None" = None
    # {total_gex, gex_regime, gamma_flip_strike} — aggregate rollup of
    # greeks_table['netGEX'], computed once in build_engine_result() via
    # _summarize_gex(). See _build_greeks_table's docstring for the
    # live-IV-per-leg convention this is now built on.
    gex_summary: dict = field(default_factory=dict)
    # Per-strike capital-weighted metrics (notional_exposure, premium_locked,
    # capital_flow, premium_turnover, delta_exposure, gamma_exposure — CE/PE
    # each), computed once via oi.capital_metrics.compute_capital_metrics()
    # off `master`. capital_flow here is day-session (ce_oi_chg-based), not
    # intraday — see capital_metrics.py's module docstring for why.
    capital_metrics: "pd.DataFrame | None" = None
    # Market Regime (Price vs Futures OI) — see analytics/market_regime.py.
    # {regime, confidence, description, price_chg_pct, fut_oi_chg_pct}.
    # Distinct from the older `fut_signal` above, which only reads futures
    # basis sign and never looked at futures OI despite the name.
    market_regime: dict = field(default_factory=dict)
    fut_oi: float = 0.0
    fut_oi_chg: float = 0.0
    fut_oi_chg_pct: float = 0.0
    # Futures day change / % change vs prev close (df_fut["Change"] /
    # df_fut["PctChange"]) — for the top-bar FUT pill, which replaces the
    # old VIX pill slot (see market-context.js).
    fut_change: float = 0.0
    fut_chg_pct: float = 0.0

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
            "gex_summary": self.gex_summary,
            "capital_metrics": self.capital_metrics,
            "market_regime": self.market_regime,
            "fut_oi": self.fut_oi,
            "fut_oi_chg": self.fut_oi_chg,
            "fut_oi_chg_pct": self.fut_oi_chg_pct,
            "fut_change": self.fut_change,
            "fut_chg_pct": self.fut_chg_pct,
        }


# ===========================================================================
# Orchestrator
# ===========================================================================

def build_engine_result(df: pd.DataFrame, df_clean: pd.DataFrame,
                          df_idx: pd.DataFrame, df_fut: pd.DataFrame,
                          df_full_history: "pd.DataFrame | None",
                          symbol: str, expiry: str, dte: int, lot_size: int,
                          n_strikes_each_side: int = 999,
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

    # ── per-symbol dividend yield (product decision 2026-08-01: NIFTY
    # q=1.23%, BANKNIFTY q=0; see DIVIDEND_YIELD_BY_SYMBOL in oi/pricing.py) ──
    q = get_dividend_yield(symbol)

    # ── master table (the one and only call to build_master_table_nse) ────
    # oi_analysis1.py computes dte internally per-row (from each row's own
    # Expiry, with an intraday fraction on expiry day) — no longer takes a
    # dte argument here.
    master = build_master_table_nse(df, spot, lot_size=lot_size, dividend_yield=q)

    # ── capital-weighted metrics (notional, premium locked, capital flow,
    # delta/gamma exposure) — one pass off `master`, consumed by every
    # downstream panel via EngineResult.capital_metrics ──────────────────
    capital_metrics = compute_capital_metrics(master, spot, lot_size)

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
    greeks_table = _build_greeks_table(master, spot, base_iv, dte, lot_size, q=q)
    gex_summary = _summarize_gex(greeks_table)

    # ── ATM Greeks (lot-adjusted, for KPI strip / exec summary / risk) ────
    t_param = max(dte / 365.0, _MIN_T_YEARS)
    atm_delta = bs_delta(spot, atm, t_param, ANNUAL_RISK_FREE_RATE, base_iv, "C", q)
    atm_theta = bs_theta(spot, atm, t_param, ANNUAL_RISK_FREE_RATE, base_iv, "C", q)
    atm_gamma = bs_gamma(spot, atm, t_param, ANNUAL_RISK_FREE_RATE, base_iv, q) * lot_size * spot / 100.0
    atm_vega = bs_vega(spot, atm, t_param, ANNUAL_RISK_FREE_RATE, base_iv, q) * lot_size

    ce_premium = df_clean["CE_LTP"].iloc[atm_idx] if "CE_LTP" in df_clean.columns else 0.0
    pe_premium = df_clean["PE_LTP"].iloc[atm_idx] if "PE_LTP" in df_clean.columns else 0.0

    # ── PCR / OI-change-PCR / walls / max pain (each computed exactly once) ─
    total_ce_oi = df_clean["CE_OI"].fillna(0).sum()
    total_pe_oi = df_clean["PE_OI"].fillna(0).sum()
    total_pcr = compute_total_pcr(df_clean)
    total_ce_oi_chg = df_clean["CE_ChgOI"].sum()
    total_pe_oi_chg = df_clean["PE_ChgOI"].sum()
    # CE_ChgOI <= 0 means calls are net unwinding chain-wide — a bullish
    # tell (short covering) on its own. Previously this branch replaced
    # the *whole ratio* with a hardcoded 0.1, which reads as extreme
    # BEARISH downstream (score_pcr/verdict_pcr) regardless of what
    # PE_ChgOI was doing — backwards. Clamping just the denominator to a
    # small positive epsilon instead lets the numerator's sign/magnitude
    # drive the result: PE_ChgOI > 0 (puts building while calls unwind)
    # saturates toward extreme bullish, as intended.
    if total_ce_oi_chg <= 0:
        total_ce_oi_chg = 0.1
    oi_chg_pcr = total_pe_oi_chg / total_ce_oi_chg

    ce_wall = (df_clean.loc[df_clean["CE_OI"].fillna(0).idxmax(), "StrikePrice"]
               if total_ce_oi > 0 else atm)
    pe_wall = (df_clean.loc[df_clean["PE_OI"].fillna(0).idxmax(), "StrikePrice"]
               if total_pe_oi > 0 else atm)

    # BUG FIX: max_pain was previously hardcoded to `atm` in option_chain.py's
    # ctx_dict; now computed for real, once, here.
    max_pain = compute_max_pain(df_clean)
    max_pain_dist = abs(spot - max_pain)

    # ── futures basis ───────────────────────────────────────────────────
    # BUG FIX: this used to fall back to `spot` whenever df_fut was missing
    # (broker futures-quote fetch failed/empty), which forces basis == 0.
    # basis > 0 was the only branch checked, so a *missing* reading and a
    # *genuinely flat* reading both fell through to "Short Buildup" —
    # silently injecting a bearish fut_score (-0.80, weighted 18% in the
    # composite) whenever a provider simply had no futures data. Track
    # availability explicitly instead: no data → fut_signal = "" so
    # decision_engine.py's `availability["futures"]` check drops the
    # signal from the composite rather than faking a direction for it.
    have_futures = df_fut is not None and not df_fut.empty and "LTP" in df_fut.columns
    futures_ltp = df_fut["LTP"].iloc[0] if have_futures else None
    basis = (futures_ltp - spot) if have_futures and futures_ltp is not None else 0.0
    if not have_futures or futures_ltp is None:
        fut_signal = ""
    else:
        fut_signal = "Long Buildup" if basis > 0 else "Short Buildup"

    # ── futures day change / % change (top-bar FUT pill) ────────────────
    # df_fut carries Change/PctChange for both the SmartAPI path
    # (fetch_futures_wide) and the public NSE path (fetch_nifty_futures).
    # BSE futures only expose "Change" (NetChange), so PctChange falls back
    # to computing from change / prev close when absent.
    fut_change = (
        float(df_fut["Change"].iloc[0])
        if df_fut is not None
        and not df_fut.empty
        and "Change" in df_fut.columns
        and df_fut["Change"].iloc[0] is not None
        else 0.0
    )
    fut_chg_pct = (
        float(df_fut["PctChange"].iloc[0])
        if df_fut is not None
        and not df_fut.empty
        and "PctChange" in df_fut.columns
        and df_fut["PctChange"].iloc[0] is not None
        else 0.0
    )
    if not fut_chg_pct and fut_change and futures_ltp and futures_ltp != fut_change:
        fut_chg_pct = round(fut_change / (futures_ltp - fut_change) * 100.0, 2)

    # ── futures OI session tracking (Market Regime input) ─────────────────
    # df_fut is None for the extra NEAR/MONTHLY expiry bundles built via
    # _build_expiry_bundle() in option_chain_json.py (those only need the
    # option chain, not a second futures fetch) — market_regime correctly
    # comes back "Indeterminate"/0-confidence for those secondary bundles
    # rather than reusing the primary tick's regime call.
    _fut_contract = (
        df_fut["Contract"].iloc[0]
        if df_fut is not None and not df_fut.empty and "Contract" in df_fut.columns
        else None
    )
    _fut_oi_raw = (
        df_fut["OI"].iloc[0]
        if df_fut is not None and not df_fut.empty and "OI" in df_fut.columns
        else None
    )
    _fut_oi_tracker_result = _get_futures_oi_tracker().update(_fut_contract, _fut_oi_raw)
    fut_oi = _fut_oi_tracker_result["fut_oi"]
    fut_oi_chg = _fut_oi_tracker_result["fut_oi_chg"]
    fut_oi_chg_pct = _fut_oi_tracker_result["fut_oi_chg_pct"]
    # has_oi_data=False whenever this tick had no usable futures OI at all
    # (df_fut missing/empty) — a genuinely-zero fut_oi_chg_pct from a real
    # OI reading is a valid "flat" regime input and shouldn't be treated
    # the same as "no data".
    _has_fut_oi_data = _fut_contract is not None and _fut_oi_raw is not None

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

    # ── Market Regime (Price vs Futures OI) ────────────────────────────────
    market_regime = classify_market_regime(
        price_chg_pct=day_chg_pct, fut_oi_chg_pct=fut_oi_chg_pct,
        has_oi_data=_has_fut_oi_data,
    )

    # ── VIX regime ───────────────────────────────────────────────────────
    # india_vix is always caller-supplied now (market_api.get_unified_market_data(),
    # called once per tick in option_chain_json.py). The old df_idx["Symbol"]=="INDIA VIX"
    # fallback was removed 2026-07-04 — it never fired (df_idx never actually
    # contained an INDIA VIX row; equity-stock-indices and allIndices are
    # different NSE endpoints) and was masking a duplicate NSE request via
    # "AllIndices" in market_api.DEFAULT_INDICES. get_unified_market_data() is the
    # single source of truth for VIX; 15.0 remains the only fallback, used
    # if that call itself fails or returns 0.
    india_vix = 15.0 if india_vix is None or india_vix <= 0 else india_vix
    vix_regime = "Low" if india_vix < T.VIX_LOW else "High" if india_vix > T.VIX_NORMAL else "Normal"

    pcr_sentiment = ("Balanced Range" if T.PCR_BEAR < total_pcr < T.PCR_BULL
                      else "PE Dominant" if total_pcr >= T.PCR_BULL
                      else "CE Dominant")
    real_picture = "Zonal Sideways" if abs(basis) < 20 else "Directional Trend Break"

    # ── OI velocity (5/15/30-min windows, from the accumulated parquet
    # history — see oi_analysis.get_oi_velocity) ───────────────────────────
    vel_df = get_oi_velocity(df_clean, symbol, expiry, windows=(5, 15, 30), lot_size=lot_size)

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
        gex_summary=gex_summary,
        capital_metrics=capital_metrics,
        market_regime=market_regime,
        fut_oi=fut_oi, fut_oi_chg=fut_oi_chg, fut_oi_chg_pct=fut_oi_chg_pct,
        fut_change=fut_change, fut_chg_pct=fut_chg_pct,
    )