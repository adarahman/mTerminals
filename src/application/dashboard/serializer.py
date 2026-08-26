import logging
from datetime import datetime, timezone
import pytz
import pandas as pd
import numpy as np
from oi.pricing import DEFAULT_BASE_IV
from oi.capital_metrics import compute_chain_metrics
from infrastructure.json_writer import write_json
from application.payload_builders.signals import build_signals
from application.payload_builders.common import (
    compact_number as fmt_k,
    formatted_number as fmt_num,
    integer as _to_int,
    nullable_rounded_number as _nullable_r,
    rounded_number as _r,
    safe_string as _safe_str,
)
from application.payload_builders.market_rows import (
    build_bid_ask_map as _build_bid_ask_map,
    build_capital_map as _build_capital_map,
    build_chain_rows as _build_chain_rows,
)
from application.payload_builders.strategies import (
    build_strategies as _build_strategies,
)
from application.payload_builders.risk import build_risk as _build_risk
from application.payload_builders.decision import build_decision
from application.market_snapshot_history import (
    compute_volume_changes as _compute_vol_changes,
    record_snapshot as _record_oi_snapshot,
)
from application.institutional_analytics_cache import (
    get_cached_bias as _get_cached_fii_dii_bias,
    get_cached_sentiment as _get_cached_fii_dii_sentiment,
)
from application.dashboard_market_metadata import (
    active_data_source as _active_data_source,
    data_sources_payload as _data_sources_payload,
    get_fno_symbols as _get_fno_symbols,
    get_symbol_display_name as _get_symbol_display_name,
)
from application.virtual_oi_service import enrich_virtual_oi
from application.payload_builders.greeks import build_greeks_rows as _greeks_rows_from_table
from application.payload_builders.oi_velocity import build_oi_velocity

logger = logging.getLogger(__name__)

def _json_default(obj):
    """Coerce numpy/pandas leftovers so orjson/stdlib can serialize the payload.

    engine.py / decision_engine routinely leave np.float64/int64/bool_ and
    occasional pandas Timestamps in nested dicts; stdlib json used to accept
    some of these via float() fallbacks in custom paths, but orjson is
    strict and raises TypeError without a default handler.
    """
    if isinstance(obj, np.generic):  # float64, int64, bool_, ...
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, set):
        return list(obj)
    # pandas NA / NaT
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    raise TypeError(f"Type is not JSON serializable: {type(obj)}")


def _write_dashboard_json(out_path: str, payload: dict) -> None:
    """Compatibility wrapper around infrastructure JSON persistence."""
    write_json(out_path, payload, default=_json_default)


# ── Panels ────────────────────────────────────────────────────────────────────
def _build_signals(ctx_dict, engine_result=None):
    """Compatibility wrapper for the focused signals builder."""
    return build_signals(ctx_dict, engine_result)




from application.payload_builders.export_sections import (
    apply_expiry_context,
    build_extra_chains,
    build_vol_oi_ratios,
)

from analytics.smart_money_summary import compute_smart_money_summary
from analytics.capital_futures_confirmation import (
    classify_capital_vs_futures, compute_capital_confirmation,
    detect_futures_options_divergence,
)
from oi.footprint_score import (
    compute_footprint_score, rank_footprint_strikes, compute_capital_concentration,
)

# ── Main export function ───────────────────────────────────────────────────────
def export_dashboard_json(
    df_clean,
    master,
    ctx_dict,
    SYMBOL,
    EXPIRY,
    dte,
    engine_result=None,
    out_path="mTerminals.json",
    expiry_dates=None,        # list of available expiry date strings
    extra_chains=None,        # dict: { "07-Jul-2026": (df_clean, master, ctx_dict, dte), ... }
    use_virtual_oi=True,      # False (from --no-virtual-oi) skips the per-strike/per-side
                               # dispatch_tick() inference loop below entirely
    contributors=None,        # list of {symbol, weightage, ltp, change, pct_change,
                               # point_impact} for SYMBOL's own index basket — see
                               # _compute_index_contributors() in option_chain_json.py.
                               # None/empty for symbols with no matching NSE basket.
    all_indices=None,         # list of {Symbol, Last Price, % Change} for ticker strip
    price_source="EQ",        # "EQ" or "FUT" — option_chain_json.PRICE_SOURCE at call
                               # time. Passed as a param (not imported directly) to
                               # avoid a circular import — option_chain_json.py is this
                               # function's only caller.
    futures_expiry=None,      # near-month futures expiry string when price_source="FUT"
                                # (option_chain_json.FUTURES_EXPIRY at call time), else None.
    pipeline_timings=None,    # dict of per-stage timings injected by the pipeline
                                # (chain/futures/quotes/engine/extraNear/extraMonthly/
                                # serialization/total) for the dashboard's perf overlay.
):
    logger.info(f"[export_dashboard_json] Assembling frontend payload for {SYMBOL}...")

    # ── 1. lastUpdated (ISO timestamp with timezone) ──────────────────────
    india_tz = pytz.timezone('Asia/Kolkata')
    last_updated = datetime.now(india_tz).isoformat(timespec='seconds')
    refresh_time = datetime.now().strftime("%H:%M:%S")   # kept for compatibility

    spot = _r(ctx_dict.get("spot", 0), 2)

    # ── 2. ATM strike ─────────────────────────────────────────────────
    # ctx_dict["atm"] is already computed by engine.py's build_engine_result
    # on every normal tick, so this fallback lookup almost never actually
    # needs to run — but it used to run an unconditional full iterrows()
    # pass over the whole master DataFrame every single tick regardless,
    # just to build a list whose result was thrown away whenever ctx_dict
    # already had "atm" (i.e. nearly always). Now it only runs — and only
    # as a vectorized column op, not a Python loop — when actually needed.
    atm_strike = _to_int(ctx_dict.get("atm", 0))
    if atm_strike == 0 and spot > 0:
        valid_strikes = master.loc[master["strike"] > 0, "strike"]
        if not valid_strikes.empty:
            atm_strike = _to_int(valid_strikes.iloc[(valid_strikes - spot).abs().argmin()])

    # ── 3. Build bid/ask lookup from df_clean ──────────────────────────
    bid_ask_map = _build_bid_ask_map(df_clean)

    # ── 4. Chain rows ─────────────────────────────────────────────────
    # footprint_score wrapping added here (not inside _build_capital_map
    # itself) so _build_capital_map stays a pure "key this df by strike"
    # helper — compute_footprint_score() is the thing that actually adds
    # the column, same separation compute_capital_metrics()/
    # compute_chain_metrics() already keep.
    capital_map = _build_capital_map(compute_footprint_score(ctx_dict.get("capital_metrics")))
    chain_rows = _build_chain_rows(master, atm_strike, bid_ask_map, capital_map)

    # ── 5. Compute Volume Changes ──────────────────────────────────────
    vol_changes = _compute_vol_changes(master, 5)   # 5-min window
    for row in chain_rows:
        sk = row["strike"]
        ce_vol_chg, pe_vol_chg = vol_changes.get(sk, (0, 0))
        row["ceVolChg"] = ce_vol_chg   # <-- NEW
        row["peVolChg"] = pe_vol_chg   # <-- NEW

    # ── 5b. Virtual OI estimation (fills NSE 3-min cooling gap) ────────
    enrich_virtual_oi(chain_rows, SYMBOL, engine_result, use_virtual_oi)


    # ── 6. Greeks rows ────────────────────────────────────────────────
    lot_size = _to_int(ctx_dict.get("lot_size", 75))
    greeks_rows = _greeks_rows_from_table(ctx_dict.get("greeks_table"))

    # ── 7. OI velocity rows ───────────────────────────────────────────
    # _record_oi_snapshot() still runs every tick — _compute_vol_changes()
    # (ceVolChg/peVolChg, computed earlier above) depends on it. Only its
    # OTHER former consumer, the OI-velocity fallback (_compute_vel_rows),
    # is scrapped — the primary vel_df path (engine.py -> oi_analysis.
    # get_oi_velocity, off the parquet-backed history) works reliably now.
    _record_oi_snapshot(master)
    oi_velocity = build_oi_velocity(engine_result)

    # ── 8. ATM CE/PE IV ───────────────────────────────────────────────
    atm_ce_iv = _nullable_r(ctx_dict.get("atm_ce_iv"), 2)
    atm_pe_iv = _nullable_r(ctx_dict.get("atm_pe_iv"), 2)
    if not atm_ce_iv or not atm_pe_iv:
        for row in chain_rows:
            if row["strike"] == atm_strike:
                if not atm_ce_iv: atm_ce_iv = row["ceIV"]
                if not atm_pe_iv: atm_pe_iv = row["peIV"]
                break

    # ── 9a. Build multi-expiry chains dict ────────────────────────────
    # chains[expiry_str] = list of chain rows  (CURRENT chain always present)
    chains_by_expiry = {str(EXPIRY): chain_rows}

    if extra_chains:
        chains_by_expiry.update(build_extra_chains(extra_chains, EXPIRY, spot))

    # ── 9. Panels ──────────────────────────────────────────────────────
    # BUGFIX: this used to be `if "expiry_label" not in ctx_dict: ...`,
    # i.e. a set-once guard. ctx_dict is a long-lived object mutated in
    # place across ticks (see the ctx_dict["atm"] note above), so that
    # guard meant expiry_label was written ONE time — on whichever tick
    # first ran this function — and never touched again, even though
    # EXPIRY (the live front-month, passed in fresh on every call from
    # the option-chain feed) keeps rolling forward as contracts expire.
    # Net effect: the option chain correctly moved from e.g. "24-Jun" to
    # "14-Jul" as the week rolled, but every strategy leg built off
    # ctx_dict["expiry_label"] (see _build_strategies) stayed pinned to
    # whatever expiry happened to be live the first time this ran —
    # producing orders against an already-expired contract that the
    # backend engine then hard-rejects. expiry_label must track EXPIRY
    # on every export, not just the first one.
    ctx_dict["expiry_label"] = str(EXPIRY)

    signals    = _build_signals(ctx_dict, engine_result)
    strategies = _build_strategies(ctx_dict, engine_result, chain_rows=chain_rows)
    risk       = _build_risk(ctx_dict, engine_result)

    # ── 9c. Chain-wide capital-weighted rollup (Executive Card) ────────
    # oi.capital_metrics.compute_chain_metrics() off the same capital_metrics
    # DataFrame chain_rows was built from above — one dict, not a 400-strike
    # table. {} when unavailable so the frontend gets a consistently-shaped
    # (empty) object instead of a missing key.
    capital_df_for_rollup = ctx_dict.get("capital_metrics")
    capital_summary = (
        compute_chain_metrics(capital_df_for_rollup)
        if capital_df_for_rollup is not None and not capital_df_for_rollup.empty
        else {}
    )

    # ── 9d. Smart Money Summary (Executive Card) ────────────────────────
    # Rollup of Market Regime + the capital_summary just computed above —
    # see analytics/smart_money_summary.py for why footprintScore isn't
    # included yet.
    smart_money_summary = compute_smart_money_summary(
        market_regime=ctx_dict.get("market_regime") or {},
        capital_summary=capital_summary,
        fut_oi_chg_pct=ctx_dict.get("fut_oi_chg_pct", 0.0),
    )

    # ── 9e. Capital vs Futures OI / Capital Confirmation / Futures-Options
    # Divergence (Phase B) ───────────────────────────────────────────────
    # All three reuse market_regime + capital_summary above and fut_oi_chg
    # off ctx_dict — no new per-strike computation. volume_ratio is a
    # lightweight total-volume/total-OI read straight off the same
    # capital_df_for_rollup used for capital_summary, not a new fetch.
    _regime_label = (ctx_dict.get("market_regime") or {}).get("regime", "Indeterminate")
    capital_vs_futures = classify_capital_vs_futures(
        net_capital_flow=capital_summary.get("net_capital_flow", 0.0),
        fut_oi_chg=ctx_dict.get("fut_oi_chg", 0.0),
    ) if capital_summary else {}

    _volume_ratio = None
    if capital_df_for_rollup is not None and not capital_df_for_rollup.empty:
        _tot_vol = float(capital_df_for_rollup["ce_volume"].sum(skipna=True) +
                          capital_df_for_rollup["pe_volume"].sum(skipna=True))
        _tot_oi = float(capital_df_for_rollup["ce_oi"].sum(skipna=True) +
                         capital_df_for_rollup["pe_oi"].sum(skipna=True))
        _volume_ratio = (_tot_vol / _tot_oi) if _tot_oi > 0 else None

    capital_confirmation = compute_capital_confirmation(
        net_capital_flow=capital_summary.get("net_capital_flow", 0.0),
        regime=_regime_label,
        price_chg_pct=ctx_dict.get("market_regime", {}).get("price_chg_pct", 0.0),
        volume_ratio=_volume_ratio,
    ) if capital_summary else {}

    futures_options_divergence = detect_futures_options_divergence(
        regime=_regime_label,
        net_capital_flow=capital_summary.get("net_capital_flow", 0.0),
    ) if capital_summary else {}

    # ── 9f. Institutional Footprint Score ranking + Capital Concentration
    # (Phase C) ──────────────────────────────────────────────────────────
    # Per-strike footprintScore itself is already attached to each chain
    # row above (see the capital_map wrapping at "4. Chain rows"); this is
    # just the top-N ranking + concentration % for the Executive card,
    # reusing that same footprint-scored df rather than recomputing it a
    # second time.
    _footprint_df = compute_footprint_score(capital_df_for_rollup) if capital_df_for_rollup is not None else None
    footprint_ranked = rank_footprint_strikes(_footprint_df, top_n=8) if _footprint_df is not None else []
    capital_concentration = compute_capital_concentration(capital_df_for_rollup, top_n=5) \
        if capital_df_for_rollup is not None else {}

    # ── 10. Full payload ──────────────────────────────────────────────
    _symbol_name = _get_symbol_display_name(SYMBOL)
    _ds_active = _active_data_source()
    _ds_payload = _data_sources_payload()
    _fno_symbols = _get_fno_symbols()
    payload = {
        "dataContract": {
            "schemaVersion": "1.0.0",
            "rowIdentity": ["symbol", "expiry", "strike"],
            "optionSideNamespaces": ["ce", "pe"],
            "units": {
                "oi": "lot_scaled_underlying_quantity",
                "changeOi": "lot_scaled_underlying_quantity",
                "volume": "contracts",
                "price": "INR_per_underlying_unit",
                "iv": "percent_points",
                "delta": "underlying_units_per_price_unit",
                "gamma": "delta_change_per_price_unit",
                "theta": "INR_per_day_per_lot",
                "vega": "INR_per_volatility_point_per_lot",
                "capital": "INR",
            },
            "nullability": {
                "missingMarketValue": None,
                "unverifiedGreekExposure": None,
                "zeroMeaning": "observed_or_computed_zero",
            },
            "provenance": {
                "chain": "exchange_or_broker_market_feed",
                "greeks": "canonical_analytics_engine",
                "capital": "oi.capital_metrics",
                "decision": "decision.DecisionEngine",
            },
            "freshness": {
                "observedAtField": "lastUpdated",
                "transportStateOwnedBy": "websocket_envelope",
            },
        },
        "pipelineTimings": pipeline_timings,

        "symbol":        str(SYMBOL),
        "symbolName":    _symbol_name,
        "spot":          spot,
        "spotChange":    _r(ctx_dict.get("spot_change",  0), 2),
        "spotChgPct":    _r(ctx_dict.get("spot_chg_pct", 0), 2),
        "spotBias":      str(ctx_dict.get("bias",        "Neutral")),
        # EQ is always the analytical source. Futures remain a separate
        # quote and confirmation input.
        "priceSource":   "EQ",
        # Near-month futures expiry used when price_source="FUT" (empty
        # string when EQ or unset) — same reasoning as priceSource above:
        # the frontend needs this to label which contract the FUT price
        # came from, not just that it's a FUT price.
        "futuresExpiry": str(futures_expiry) if futures_expiry else "",
        # ── DATA SOURCE (runtime-switchable, see ws_server_live.py's
        # ?dataSource= handler) ───────────────────────────────────────────
        # dataSource: the ACTIVE provider key; dataSources: every selectable
        # provider + its capability/status for the Dashboard's picker.
        "dataSource":    _ds_active,
        "dataSources":   _ds_payload,
        "expiry":        str(EXPIRY),
        "expiryDates":   expiry_dates or [],   # <-- NEW: full list
        "dte":           _to_int(dte),
        "atm":           atm_strike,

        "future":        _r(spot + _r(ctx_dict.get("basis", 0), 2), 2),
        "basis":         _r(ctx_dict.get("basis", 0), 2),
        "futureChange":  _r(ctx_dict.get("fut_change", 0), 2),
        "futureChgPct":  _r(ctx_dict.get("fut_chg_pct", 0), 2),

        "maxPain":       _to_int(ctx_dict.get("max_pain",      0)),
        "maxPainDist":   _r(ctx_dict.get("max_pain_dist",      0), 2),
        "ceWall":        _to_int(ctx_dict.get("ce_wall",        0)),
        "peWall":        _to_int(ctx_dict.get("pe_wall",        0)),

        "totalPCR":      _r(ctx_dict.get("total_pcr",    1.0), 2),
        "oiChgPCR":      _r(ctx_dict.get("oi_chg_pcr",   0.0), 2),
        "pcrSentiment":  str(ctx_dict.get("pcr_sentiment", "Balanced")),

        # ── capital-weighted chain rollup (Executive Card / Smart Money) ──
        # PE/CE ratio weighted by premium locked rather than raw OI count —
        # compare against totalPCR above; divergence between the two is
        # itself a signal (see oi.capital_metrics.compute_chain_metrics
        # docstring). netGammaExposureCapital is CE-minus-PE (same
        # differencing convention as the existing raw-OI netGEX), NOT a sum.
        # capitalCeWallStrike/capitalPeWallStrike are the highest-premium
        # strikes and can differ from ceWall/peWall above (highest raw OI) —
        # frontend should label the two distinctly, not overwrite one with
        # the other.
        "capitalPCR":            _r(capital_summary.get("capital_pcr", 0.0), 2),
        "netPremiumLocked":      _r(capital_summary.get("net_premium_locked", 0.0), 2),
        "netCapitalFlow":        _r(capital_summary.get("net_capital_flow", 0.0), 2),
        "netGammaExposureCapital": _nullable_r(capital_summary.get("net_gamma_exposure"), 2),
        "netDeltaExposureCapital": _nullable_r(capital_summary.get("net_delta_exposure"), 2),
        "totalNotionalExposureCapital": _r(
            capital_summary.get("total_ce_notional_exposure", 0.0)
            + capital_summary.get("total_pe_notional_exposure", 0.0), 2
        ),
        "totalPremiumTurnoverCapital": _r(
            capital_summary.get("total_ce_premium_turnover", 0.0)
            + capital_summary.get("total_pe_premium_turnover", 0.0), 2
        ),
        "totalPremiumLockedCapital": _r(
            capital_summary.get("total_ce_premium_locked", 0.0)
            + capital_summary.get("total_pe_premium_locked", 0.0), 2
        ),
        "capitalCeWallStrike":   _to_int(capital_summary.get("ce_capital_wall_strike") or 0),
        "capitalPeWallStrike":   _to_int(capital_summary.get("pe_capital_wall_strike") or 0),

        "atmIV":         _r(ctx_dict.get("base_iv", DEFAULT_BASE_IV) * 100, 2),
        "atmCeIV":       atm_ce_iv,
        "atmPeIV":       atm_pe_iv,
        "atmSkew":       _r(ctx_dict.get("atm_skew", 0.0), 2),
        "ivRank":        _r(ctx_dict.get("iv_rank",  35.0), 2),
        "hv30":          _r(ctx_dict.get("hv30",     15.0), 1),
        "indiaVix":      _r(ctx_dict.get("india_vix",14.0), 1),
        "indiaVixChgPct": _r(ctx_dict.get("india_vix_chg_pct", 0.0), 2),
        "vixRegime":     str(ctx_dict.get("vix_regime", "Normal")),

        "futSignal":     str(ctx_dict.get("fut_signal",  "Neutral")),
        "trapWarn":      str(ctx_dict.get("trap_warn",   "None")),
        "compositeBias": str(ctx_dict.get("bias",        "Neutral")),

        "callPremium":   _r(ctx_dict.get("ce_premium",  0.0), 2),
        "putPremium":    _r(ctx_dict.get("pe_premium",  0.0), 2),
        "straddle":      _r(ctx_dict.get("ce_premium",  0.0) + ctx_dict.get("pe_premium", 0.0), 2),
        "atmDelta":      _r(ctx_dict.get("atm_delta",   0.5),  4),
        "atmGamma":      _r(ctx_dict.get("atm_gamma",   0.0),  4),
        "atmTheta":      _r(ctx_dict.get("atm_theta",   0.0),  4),
        "atmVega":       _r(ctx_dict.get("atm_vega",    0.0),  4),

        "refreshTime":   refresh_time,
        "lastUpdated":   last_updated,   # <-- NEW: full ISO timestamp

        "signals":       signals,
        "strategies":    strategies,
        "risk":          risk,

        "oiVelocity":    oi_velocity,
        "greeks":        greeks_rows,
        "chain":         chain_rows,
        # ── multi-expiry chain store ──────────────────────────────────
        # chains[expiry_str] → array of chain rows (same shape as "chain")
        # chainMeta[expiry_str] → atm, dte, walls, pcr, iv for that expiry
        "chains":        {k: v for k, v in chains_by_expiry.items() if not k.startswith("__meta__")},
        "chainMeta":     {k[8:]: v for k, v in chains_by_expiry.items() if k.startswith("__meta__")},
        # ── Top Drivers/Draggers (Dashboard's exec-grid "Top Movers" card) ──
        # [] for symbols with no matching NSE index basket (e.g. BSE symbols).
        "contributors":  contributors or [],
        # ── FII/DII participant positioning sentiment (display only) ──
        # Lagged to the prior trading day's EOD file (see fii_dii_sentiment.py
        # docstring) — never same-day, to avoid lookahead. {} if unavailable
        # (fetch hasn't run yet, module missing, or no file for that date).
        "fiiDiiSentiment": _get_cached_fii_dii_sentiment() or {},
        # ── Combined cash+F&O FII/DII bias summary (display only) ──
        # Same source ws_server_live.py's bridge_loop() computes for the
        # /dashboard-relay feed (fiidii-report.js's fdRenderBias) — cached
        # here on the same once/day cadence so the main dashboard's FII/DII
        # summary card can show it without needing that relay connection.
        "fiiDiiBias": _get_cached_fii_dii_bias() or {},
        # ── Market Regime (Price vs Futures OI) — Executive card ──────────
        # Tick-fresh (not day-cached like fiiDiiBias above) — see
        # analytics/market_regime.py. {} only if engine_result somehow ran
        # without this field (defensive; market_regime always builds one).
        "marketRegime": ctx_dict.get("market_regime") or {},
        "futOi":        _r(ctx_dict.get("fut_oi", 0.0), 0),
        "futOiChg":     _r(ctx_dict.get("fut_oi_chg", 0.0), 0),
        "futOiChgPct":  _r(ctx_dict.get("fut_oi_chg_pct", 0.0), 2),
        "smartMoneySummary": smart_money_summary,
        "capitalVsFutures": capital_vs_futures,
        "capitalConfirmation": capital_confirmation,
        "futuresOptionsDivergence": futures_options_divergence,
        "footprintRanked": footprint_ranked,
        "capitalConcentration": capital_concentration,
        # ── Index ticker strip data (NIFTY/BANKNIFTY/MIDCPNIFTY/SENSEX) ──
        # Fetched from NSE allIndices endpoint + BSE getScripHeaderData when relevant
        "allIndices": all_indices or [],
        # ── Top-bar symbol picker options ──────────────────────────────
        # {"indices": [...], "stocks": [...]} covering EVERY NSE/BSE
        # underlying with live F&O contracts (not just the 6-symbol
        # COMMON_SYMBOLS list dashboard.js used to hardcode) — see
        # smartapi_client.get_fno_underlyings() / renderSymbolOptions()
        # in chain-views.js.
        "fnoSymbols": _fno_symbols,
    }

    # ── 10b. Simulator support fields (V51Pro) ──────────────────────────
    # The Institutional F&O Simulator section reads d.ctx.{spot,atm,baseIv}
    # and d.volOiRatios.{strike}. Neither existed in the payload before —
    # these are pure aliases/derivations off data already computed above,
    # so nothing else changes.
    payload["ctx"] = {
        "spot":   payload["spot"],
        "atm":    payload["atm"],
        "baseIv": payload["atmIV"],
    }

    vol_oi_ratios = build_vol_oi_ratios(chain_rows)
    payload["volOiRatios"] = vol_oi_ratios

    # ── 11. ExpiryContext (current / near / monthly / far) ─────────────────
    apply_expiry_context(payload, expiry_dates, EXPIRY)

    # ── 12. Decision block ──────────────────────────────────────────────────
    if engine_result is not None:
        payload["decision"] = build_decision(
            engine_result,
            ctx_dict,
            last_updated,
            payload.get("symbol", ""),
            payload.get("expiry", ""),
        )


    # orjson is ~5-10× faster than stdlib json for the dashboard payload
    # and is already a hard dependency of ws_server_live.py. Fall back to
    # json.dump if orjson isn't available OR if a non-serializable type
    # still sneaks into the payload after the default coercer runs
    # (engine/decision paths routinely leave numpy scalars / Timestamps).
    _write_dashboard_json(out_path, payload)

    vel_counts = [len(b["rows"]) for b in oi_velocity]
    vol_count = len([r for r in chain_rows if r.get("ceVolChg") != 0 or r.get("peVolChg") != 0])
    has_decision = "decision" in payload
    logger.info(f"[export_dashboard_json] JSON exported → {out_path}  ({len(chain_rows)} strikes, "
                f"{len(strategies)} strategies, {len(signals)} signals, "
                f"vel rows 5m/15m/30m: {vel_counts[0]}/{vel_counts[1]}/{vel_counts[2]}, "
                f"vol changes: {vol_count} strikes, decision={has_decision})")
    return payload
