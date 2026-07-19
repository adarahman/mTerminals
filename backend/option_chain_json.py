import math
import os
import sys
import argparse
import traceback
import pandas as pd
import time
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from engine import build_engine_result
from oi_analysis import (
    build_oi_history, compute_dte,
    read_last_json_snapshot, append_json_history,
)
from smartapi_pipeline_adapter import (
    fetch_option_chain_wide,
    fetch_futures_wide,
    fetch_all_pills_and_vix_batched,
    fetch_vix_smartapi,
    get_available_expiries,
    fetch_ticker_payload_smartapi,
    fetch_sensex_ticker_smartapi,
)
# No manual session object needed here — smartapi_pipeline_adapter.py's
# functions call through to mTerminals.smartapi_client's module-level
# `_session` singleton (SmartApiSession), which handles login/token-refresh
# itself. Imported package-qualified (mTerminals.smartapi_client) to match
# ws_server_live.py's own import style exactly — a flat `import
# smartapi_client` here would create a SECOND, distinct module object (and
# therefore a second _session singleton) even though it's the same file on
# disk, causing two independent logins to race each other on startup.

from market_api import fetch_all_indices
from expiry_manager import make_expiry_manager

# ─── df_idx TTL cache ────────────────────────────────────────────────
# fetch_all_indices() is the one NSE HTTP call with no SmartAPI equivalent
# — it's what feeds _compute_index_contributors()'s ffmc weighting AND the
# Volume/Value merge into all_indices (SmartAPI's index ltpData has neither
# field). Ticker-pill LTP/change values used to piggyback on this same
# call for free; they're now sourced from SmartAPI directly (see
# fetch_ticker_payload_smartapi import above) and no longer need df_idx.
#
# That means df_idx's only remaining consumers — ffmc contributor weights
# and Volume/Value — don't need per-tick (POLL_SECONDS) freshness the way
# live LTP does: per-stock free-float weighting and session volume totals
# don't meaningfully change second to second. So this call is decoupled
# from the main poll loop onto its own TTL, cutting real NSE HTTP volume
# without touching anything that reads df_idx downstream (same DataFrame,
# just refreshed less often).
DF_IDX_TTL_SECONDS = 20
_DF_IDX_CACHE = {"df": None, "ts": 0.0}


def _fetch_all_indices_cached():
    now = time.time()
    if _DF_IDX_CACHE["df"] is None or (now - _DF_IDX_CACHE["ts"]) >= DF_IDX_TTL_SECONDS:
        _DF_IDX_CACHE["df"] = fetch_all_indices()
        _DF_IDX_CACHE["ts"] = now
    return _DF_IDX_CACHE["df"]

# ─── Virtual OI estimator coordinator loader ──────────────────────────
# Import conditionally to avoid loading models when --no-virtual-oi is set
load_virtual_oi_coordinator = None

# =====================================================================
# NSE/BSE Option Chain Downloader (JSON Only Pipeline)
# =====================================================================

def _nearest_weekday(target_weekday):
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    return (today + timedelta(days=days_ahead)).strftime("%d-%b-%Y")

def _nearest_Tuesday(): return _nearest_weekday(1)
def _nearest_Thursday(): return _nearest_weekday(3)

def _nearest_monthly_thursday():
    today = date.today()
    def last_thursday(year, month):
        if month == 12:
            last_day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)
        offset = (last_day.weekday() - 3) % 7
        return last_day - timedelta(days=offset)

    lt = last_thursday(today.year, today.month)
    if lt < today:
        lt = last_thursday(today.year, 12 if today.month == 12 else today.month + 1)
    return lt.strftime("%d-%b-%Y")

BSE_EXPIRY_DEFAULT = {
    "SENSEX"   : _nearest_Thursday,
    "BANKEX"   : _nearest_monthly_thursday,
    "SENSEX50" : _nearest_monthly_thursday,
    "PNB"      : _nearest_monthly_thursday,
}

# BSE has no public "available expiries" endpoint wired up in market_api.py
# (unlike NSE, whose option-chain response includes records.expiryDates).
# Since BSE index derivatives trade a fixed weekly/monthly cadence, we can
# synthesize the same kind of list structurally instead of calling an API —
# expiry_manager.py only needs a sorted list of future "DD-Mon-YYYY" dates
# to derive its CURRENT/NEAR/MONTHLY/FAR slots, it doesn't care where the
# list came from.
def _generate_bse_expiry_series(symbol, count=8):
    today = date.today()
    if symbol == "SENSEX":
        # weekly Thursday cadence
        first = _nearest_weekday(3)
        first_d = datetime.strptime(first, "%d-%b-%Y").date()
        return [(first_d + timedelta(weeks=i)).strftime("%d-%b-%Y") for i in range(count)]
    else:
        # BANKEX / SENSEX50 / PNB: monthly (last Thursday of month) cadence only
        dates = []
        year, month = today.year, today.month
        for _ in range(count):
            if month == 12:
                last_day = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                last_day = date(year, month + 1, 1) - timedelta(days=1)
            offset = (last_day.weekday() - 3) % 7
            lt = last_day - timedelta(days=offset)
            if lt >= today:
                dates.append(lt.strftime("%d-%b-%Y"))
            month += 1
            if month > 12:
                month = 1
                year += 1
        return dates

BSE_SCRIP_CD = {
    "SENSEX": "1", "BANKEX": "12", "SENSEX50": "47", "PNB": "532461",
}

_parser = argparse.ArgumentParser(prog="option_chain_json", add_help=True)
_parser.add_argument("--exchange", default="NSE", choices=["NSE", "BSE"])
_parser.add_argument("--symbol",   default="NIFTY")
_parser.add_argument("--interval", default=0, type=int)
_parser.add_argument("--no-extra-chains", action="store_true", help="Disable multi-expiry chains for faster performance")
_parser.add_argument("--strict-expiry", action="store_true", help="Don't auto-resolve to different expiry if requested expiry has no data")
_parser.add_argument("--no-virtual-oi", action="store_true", help="Disable VirtualOI model inference for faster performance")

_pre, _ = _parser.parse_known_args()
_sym = (_pre.symbol or "NIFTY").strip().upper()
_default_expiry = BSE_EXPIRY_DEFAULT.get(_sym, _nearest_Thursday)() if _sym in {"SENSEX", "BANKEX", "SENSEX50"} else _nearest_Tuesday()

_parser.add_argument("--expiry", default=_default_expiry, help="Expiry DD-Mmm-YYYY")
_args, _unknown = _parser.parse_known_args()

EXCHANGE      = _args.exchange.strip().upper()
SYMBOL        = _args.symbol.strip().upper()
EXPIRY        = _args.expiry.strip()
LOOP_INTERVAL = _args.interval
NO_EXTRA_CHAINS = _args.no_extra_chains
STRICT_EXPIRY  = _args.strict_expiry
NO_VIRTUAL_OI  = _args.no_virtual_oi

# How many strikes each side of ATM the engine computes Greeks/OI-velocity/
# signal analytics for. Standalone default is 10; when this module is
# imported by ws_server_live.py, it overwrites this attribute post-import
# (option_chain_json.STRIKES_EACH_SIDE = ...) based on --no-smartapi /
# --strikes-each-side. Both call sites below must read this module-level
# name at call time (not bake in a literal default) or that override is a
# no-op — which was the bug: strikes stayed pinned at 10 even under
# --no-smartapi, where 50 was intended.
STRIKES_EACH_SIDE = 10

print("\n=== LIGHTWEIGHT JSON OPTIONS PIPELINE INITIALIZATION ===")
print(f"    Exchange: {EXCHANGE} | Symbol: {SYMBOL} | Expiry: {EXPIRY}")
print(f"    Loop    : {'every ' + str(LOOP_INTERVAL) + ' min' if LOOP_INTERVAL > 0 else 'single run'}\n")

# =====================================================================
# FETCH, PARSE & STRUCTURING
# =====================================================================

def _fetch_and_parse(symbol, expiry, exchange, strict_expiry=False):
    if exchange == "BSE":
        # Was fetch_bse_json_options() — BSE's own JSON option-chain HTTP
        # endpoint. fetch_option_chain_wide() is exchange-parametrized
        # (STRIKE_INTERVALS/_get_strike_interval already cover SENSEX/
        # BANKEX/SENSEX50), so exchange="BFO" is a genuine drop-in: same
        # output columns (StrikePrice/Expiry/Spot/Symbol/CE_*/PE_*) this
        # branch used to hand-build via the Strike->StrikePrice rename
        # below, now produced natively.
        df = fetch_option_chain_wide(symbol, expiry, exchange="BFO")
        if df.empty:
            raise RuntimeError(f"SmartAPI BFO chain fetch empty for {symbol} {expiry}")
        spot = df["Spot"].iloc[0] if "Spot" in df.columns else 0.0
        expiry_dates = _generate_bse_expiry_series(symbol)
        return df, spot, expiry_dates
    else:
        expiry_dates = get_available_expiries(symbol)
        resolved = expiry
        if resolved not in expiry_dates:
            if strict_expiry:
                raise RuntimeError(f"Requested expiry '{expiry}' has no data. Available: {expiry_dates}")
            today = date.today()
            future = [e for e in expiry_dates
                      if pd.to_datetime(e, format="%d-%b-%Y").date() >= today]
            if not future:
                raise RuntimeError(f"No future expiries available for {symbol}")
            resolved = future[0]
            print(f"[Expiry] '{expiry}' unavailable → selected: '{resolved}'")
        df = fetch_option_chain_wide(symbol, resolved)
        if df.empty:
            raise RuntimeError(f"SmartAPI chain fetch empty for {symbol} {resolved}")
        spot = df["Spot"].iloc[0] if "Spot" in df.columns else 0.0
        return df, spot, resolved, expiry_dates

def _resolve_expiry(data, requested_expiry, strict=False):
    available = data["records"].get("expiryDates", [])
    if requested_expiry and data["records"].get("data", []):
        return requested_expiry
    if strict:
        raise RuntimeError(f"Requested expiry '{requested_expiry}' has no data. Available: {available}")
    today = date.today()
    for exp in available:
        try:
            if pd.to_datetime(exp, format="%d-%b-%Y").date() >= today:
                print(f"[Expiry] '{requested_expiry}' empty → selected: '{exp}'")
                return exp
        except Exception: continue
    raise RuntimeError(f"No valid future expiry found: {available}")

# Emergency fallback only — live lot sizes come from FUTSTK/FUTIDX rows in
# the AngelOne master via smartapi_instruments.get_lot_size(). These static
# numbers go stale on every NSE quarterly lot revision; never add stocks
# here hoping to cover the universe (there are 200+).
_STATIC_LOT_SIZES = {
    "NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120,
    "SENSEX": 20, "BANKEX": 30, "SENSEX50": 75, "PNB": 8000,
}


class _LiveLotSizes:
    """dict-like shim so existing LOT_SIZES.get(sym, default) call sites
    resolve through FUT-derived lot sizes without a hard-coded table."""

    def get(self, symbol, default=65):
        sym = (symbol or "").upper()
        try:
            from smartapi_instruments import get_lot_size
            return get_lot_size(sym)
        except Exception:
            return _STATIC_LOT_SIZES.get(sym, default)

    def __getitem__(self, symbol):
        sym = (symbol or "").upper()
        try:
            from smartapi_instruments import get_lot_size
            return get_lot_size(sym)
        except Exception:
            if sym in _STATIC_LOT_SIZES:
                return _STATIC_LOT_SIZES[sym]
            raise KeyError(symbol)

    def __contains__(self, symbol):
        sym = (symbol or "").upper()
        try:
            from smartapi_instruments import get_lot_size
            get_lot_size(sym)
            return True
        except Exception:
            return sym in _STATIC_LOT_SIZES


LOT_SIZES = _LiveLotSizes()

# Maps our dashboard SYMBOL to the literal "Index" tag used inside df_idx
# (i.e. the exact string passed to fetch_fno_index() as part of
# DEFAULT_INDICES in market_api.py). Only NSE index-basket symbols have an
# entry — BSE symbols (SENSEX/BANKEX/SENSEX50/PNB) aren't in DEFAULT_INDICES,
# so contributors will legitimately be empty for those.
SYMBOL_TO_INDEX_BASKET = {
    "NIFTY":      "NIFTY 50",
    "BANKNIFTY":  "NIFTY BANK",
    "FINNIFTY":   "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
}


def _compute_index_contributors(df_idx, symbol, index_spot):
    """Top drivers/draggers for `symbol`'s own index basket, derived from
    df_idx (already fetched once per tick via fetch_all_indices() — no new
    network call here). Weight is approximated live from free-float market
    cap (ffmc_i / sum(ffmc)) when NSE actually returns that field; falls
    back to equal weighting across the basket if it doesn't (some NSE
    endpoint variants omit ffmc), so the widget still populates rather
    than silently staying empty. Prints a one-line reason whenever it
    returns [] or falls back, so this is diagnosable from the console.
    """
    basket = SYMBOL_TO_INDEX_BASKET.get(symbol)
    if not basket:
        print(f"[Contributors] Skip: no index basket mapped for SYMBOL='{symbol}' (expected for BSE symbols).")
        return []
    if df_idx is None or df_idx.empty or "Index" not in df_idx.columns:
        print(f"[Contributors] Skip: df_idx is empty or missing 'Index' column.")
        return []

    rows = df_idx[df_idx["Index"] == basket]
    if rows.empty:
        available = sorted(df_idx["Index"].unique().tolist())
        print(f"[Contributors] Skip: no rows tagged Index='{basket}' in df_idx. Available Index tags: {available}")
        return []

    ffmc_vals = rows["ffmc"] if "ffmc" in rows.columns else pd.Series(dtype=float)
    ffmc_numeric = pd.to_numeric(ffmc_vals, errors="coerce")
    valid_ffmc = ffmc_numeric.notna() & (ffmc_numeric > 0)
    total_ffmc = float(ffmc_numeric.loc[valid_ffmc].sum()) if valid_ffmc.any() else 0.0

    use_equal_weight = not total_ffmc
    if use_equal_weight:
        n = len(rows)
        print(f"[Contributors] WARNING: 'ffmc' missing/zero for all {n} rows in '{basket}' "
              f"(NSE didn't return it for this endpoint) — falling back to equal weighting "
              f"({round(100/n, 2)}% each). Point-impact ranking will be less accurate than "
              f"true free-float weight until this is investigated.")

    contributors = []
    n_rows = len(rows)
    for _, r in rows.iterrows():
        ffmc = float(r.get("ffmc") or 0)
        if use_equal_weight or (not math.isfinite(ffmc) or ffmc <= 0):
            weight = 100.0 / n_rows if n_rows else 0
        else:
            weight = (ffmc / total_ffmc) * 100
        pct_change = float(r.get("% Change") or 0)
        if not math.isfinite(pct_change):
            pct_change = 0.0
        point_impact = round((pct_change * weight * index_spot) / 10000, 2)
        if not math.isfinite(point_impact):
            point_impact = 0.0
        contributors.append({
            "symbol":       r.get("Symbol"),
            "weightage":    round(weight, 2),
            "ltp":          r.get("Last Price"),
            "change":       r.get("Change"),
            "pct_change":   pct_change,
            "point_impact": point_impact,
        })

    if not contributors:
        print(f"[Contributors] Skip: '{basket}' had {n_rows} row(s) but none produced a usable contributor entry.")

    contributors.sort(key=lambda c: abs(c["point_impact"]), reverse=True)
    return contributors

def _build_expiry_bundle(symbol, expiry, exchange="NSE", strict_expiry=False, **engine_kwargs):
    if exchange == "BSE":
        df, spot, _ = _fetch_and_parse(symbol, expiry, exchange, strict_expiry)
        resolved = expiry
    else:
        df, spot, resolved, _ = _fetch_and_parse(symbol, expiry, exchange, strict_expiry)

    df_clean = df.dropna(subset=["StrikePrice"]).drop_duplicates(subset=["StrikePrice"]).sort_values("StrikePrice").copy()
    dte = compute_dte(resolved)
    
    engine_result = build_engine_result(
        df=df, df_clean=df_clean, df_idx=None, df_fut=None, df_full_history=None,
        symbol=symbol, expiry=resolved, dte=dte, lot_size=engine_kwargs.pop("lot_size", LOT_SIZES.get(symbol, 65)),
        n_strikes_each_side=engine_kwargs.pop("n_strikes_each_side", STRIKES_EACH_SIDE),
        velocity_window_minutes=engine_kwargs.pop("velocity_window_minutes", 15),
        **engine_kwargs
    )
    return df_clean, engine_result.master, engine_result.to_ctx_dict(), dte, resolved

# =====================================================================
# PIPELINE EXECUTION
# =====================================================================

def main():
    global EXPIRY
    _BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}
    EXCHANGE = "BSE" if SYMBOL in _BSE_SYMBOLS else "NSE"

    # NOTE (2026-07-04): previously this block did
    #     coordinator = load_virtual_oi_coordinator("model_registry")
    # every tick — a joblib.load() disk read + deserialization on every poll
    # cycle, whose result (`coordinator`) was never passed to anything and
    # was never used. The real, actually-used coordinator lives in
    # mTerminals_json.py as a module-level _VOI_COORDINATOR, loaded exactly
    # once per process. --no-virtual-oi is now honored by passing
    # use_virtual_oi through to export_dashboard_json() below instead.

    try:
        # ── Fetch chain + futures + all-indices + VIX + ticker pills concurrently ──
        # These five NSE/BSE calls are independent of each other (futures/
        # indices/VIX/ticker-pills don't need the option-chain result), so
        # running them one after another was pure serial waiting. This was
        # the single biggest contributor to per-tick latency. (Used to be six
        # — a separate fetch_india_vix() call was folded into
        # get_unified_market_data() below, removing a redundant NSE round-trip.)
        with ThreadPoolExecutor(max_workers=5) as ex:
            if EXCHANGE == "BSE":
                fut_chain = ex.submit(_fetch_and_parse, SYMBOL, EXPIRY, "BSE", STRICT_EXPIRY)
                # Was fetch_bse_futures() (BSE HTTP + scrip code lookup).
                # fetch_futures_wide() already resolves FUTIDX contracts
                # generically off the ScripMaster (_get_futures_contract),
                # same as the NSE branch below — exchange="BFO" is the only
                # difference.
                fut_fut   = ex.submit(fetch_futures_wide, SYMBOL, EXPIRY, exchange="BFO")
            else:
                fut_chain = ex.submit(_fetch_and_parse, SYMBOL, EXPIRY, "NSE", STRICT_EXPIRY)
                fut_fut   = ex.submit(fetch_futures_wide, SYMBOL, EXPIRY)
            # df_idx now TTL-cached (see _fetch_all_indices_cached above) —
            # still submitted through the pool each tick, but only actually
            # hits NSE once every DF_IDX_TTL_SECONDS; other ticks get the
            # cached DataFrame back instantly.
            fut_idx = ex.submit(_fetch_all_indices_cached)
            # Batched replacement for 6 separate ltpData calls (each
            # throttled at 1.0s globally -> ~6s/tick) with 2 getMarketData
            # calls (~0.35s each). Submitted here so it overlaps with the
            # chain/futures/idx fetches above instead of adding wall-clock
            # time; .result() below blocks only this thread until it's
            # ready, then the three wrapper calls just read the cache.
            fut_batch = ex.submit(fetch_all_pills_and_vix_batched)
            fut_batch.result()
            # Ticker pills (NIFTY/BANKNIFTY/MIDCPNIFTY/FINNIFTY) — now pure
            # SmartAPI REST via fetch_ticker_payload_smartapi(), independent
            # of df_idx entirely. Unconditional every tick, same reasoning
            # as before: pills must stay populated even while the active
            # symbol is SENSEX/BANKEX.
            fut_ticker = ex.submit(fetch_ticker_payload_smartapi)
            fut_unified = ex.submit(fetch_vix_smartapi)
            # SENSEX pill — also SmartAPI now (INDEX_TOKENS), replacing the
            # old BSE getScripHeaderData round-trip. Still unconditional so
            # it stays live while viewing NSE symbols.
            fut_sensex = ex.submit(fetch_sensex_ticker_smartapi)

            if EXCHANGE == "BSE":
                df, spot, expiry_dates = fut_chain.result()
            else:
                df, spot, resolved, expiry_dates = fut_chain.result()
                if resolved != EXPIRY: EXPIRY = resolved
            df_fut  = fut_fut.result()
            df_idx  = fut_idx.result()

            _live_vix, _live_vix_chg_pct = fut_unified.result()
            _live_vix = _live_vix or 0.0
            sensex_quote = fut_sensex.result()
            ticker_payload = fut_ticker.result()
            all_indices = list(ticker_payload)
            if sensex_quote:
                all_indices.append(sensex_quote)

            # Merge in real Volume/Value from df_idx (already fetched above
            # via fut_idx — no new network call). get_unified_market_data()'s
            # own /api/allIndices source reports Volume/Value as a hardcoded
            # 0 on every index-level row (an index isn't itself a traded
            # instrument), so ticker_payload's entries never carry usable
            # volume — but df_idx comes from equity-stock-indices, which
            # does include the index's own aggregate row with real
            # session-cumulative totals (same numbers NSE's own live-market
            # page shows). Matched on Symbol, which is already the same
            # INDEX_RENAME'd string on both sides (e.g. "NIFTY", "BANKNIFTY").
            # Frontend: dashboard.js's price chart reads Value/Volume off
            # this same allIndices payload to compute a running VWAP.
            if df_idx is not None and not df_idx.empty and "Symbol" in df_idx.columns:
                vol_map = (
                    df_idx.dropna(subset=["Volume"])
                    .drop_duplicates(subset=["Symbol"], keep="first")
                    .set_index("Symbol")[["Volume", "Value"]]
                    .to_dict("index")
                )
                for entry in all_indices:
                    row = vol_map.get(entry.get("Symbol"))
                    if row:
                        entry["Volume"] = row["Volume"]
                        entry["Value"] = row["Value"]

        if spot == 0 or spot is None:
            print("Error: Invalid Spot Price. Core calculations aborted.")
            return

        # Derived from df_idx (already fetched above, no new network call) —
        # empty list for symbols with no matching NSE index basket (BSE, etc.)
        contributors = _compute_index_contributors(df_idx, SYMBOL, spot)

        dte = compute_dte(EXPIRY)
        df_clean = df.dropna(subset=["StrikePrice"]).drop_duplicates(subset=["StrikePrice"]).sort_values("StrikePrice").copy()

        # BUGFIX: em (ExpiryManager) used to only get built inside the
        # `if not NO_EXTRA_CHAINS` block below, purely to fetch extra chain
        # bundles for the NEAR/MONTHLY tabs — its correctly-computed,
        # future-only NEAR/MONTHLY date strings never made it to
        # build_engine_result(). engine.py's Calendar Spread strategy
        # accepts near_expiry/far_expiry params, but since nothing ever
        # passed them, it silently fell back to placeholder text labels
        # "NEAR"/"FAR" on the leg dicts — pushing the job of resolving a
        # real date onto the frontend, which has to guess from raw
        # expiryDates array position (dates[0] / dates[-1]) instead of
        # using ExpiryManager's actual, already-verified, future-filtered
        # slot dates. That guess can land on a stale or otherwise wrong
        # entry depending on the raw array's order. Build em unconditionally
        # (it's pure computation off the already-fetched expiry_dates list,
        # no extra network cost) so real dates flow through even when
        # --no-extra-chains is set and extra_chains itself is skipped.
        em = None
        if expiry_dates:
            try:
                em = make_expiry_manager(expiry_dates)
            except Exception as e:
                print(f"[ExpiryManager] Context skip ({e})")

        # Extra chains management — NEAR and MONTHLY are independent of each
        # other, so build them concurrently instead of one after another.
        extra_chains = {}
        if not NO_EXTRA_CHAINS and em is not None:
            try:
                slots = [
                    (slot_name, slot) for slot_name, slot in
                    [("NEAR", em.context.near), ("MONTHLY", em.context.monthly)]
                    if slot and slot.date_str != str(EXPIRY)
                ]
                if slots:
                    with ThreadPoolExecutor(max_workers=len(slots)) as ex2:
                        futs = {
                            ex2.submit(_build_expiry_bundle, SYMBOL, slot.date_str, EXCHANGE): (slot_name, slot)
                            for slot_name, slot in slots
                        }
                        for f in as_completed(futs):
                            slot_name, slot = futs[f]
                            try:
                                n_df, n_master, n_ctx, n_dte, _ = f.result()
                                extra_chains[slot.date_str] = (n_df, n_master, n_ctx, n_dte)
                            except Exception as e:
                                print(f"[{slot_name}] Skip extra bundle ({e})")
            except Exception as e:
                print(f"[ExtraChains] Skip ({e})")

        # Fallback to local JSON snap logs for historical OI analysis
        prev_json_poll = read_last_json_snapshot(SYMBOL)
        history_df = build_oi_history(df_clean, SYMBOL, prev_poll=prev_json_poll)
        append_json_history(history_df)

        # Calendar spread convention: sell the current active expiry (front
        # week/month you're already trading), buy the next MONTHLY expiry —
        # both are real, verified, future dates straight from ExpiryManager.
        # Falls back to "" (→ engine.py's "NEAR"/"FAR" text placeholders)
        # only if em wasn't available at all, never to a stale value.
        _near_expiry_str = em.context.current.date_str if em is not None else ""
        _far_expiry_str  = (
            em.context.monthly.date_str if em is not None and em.context.monthly
            else em.context.far.date_str if em is not None and em.context.far
            else ""
        )

        engine_result = build_engine_result(
            df=df, df_clean=df_clean, df_idx=df_idx, df_fut=df_fut, df_full_history=history_df,
            symbol=SYMBOL, expiry=EXPIRY, dte=dte, lot_size=LOT_SIZES.get(SYMBOL, 65),
            n_strikes_each_side=STRIKES_EACH_SIDE, velocity_window_minutes=15, india_vix=_live_vix,
            india_vix_chg_pct=_live_vix_chg_pct,
            near_expiry=_near_expiry_str, far_expiry=_far_expiry_str
        )


        from mTerminals_json import export_dashboard_json
        ctx_dict = engine_result.to_ctx_dict()

        # SENSEX never appears in df_idx (fetch_all_indices()/DEFAULT_INDICES
        # is NSE-only), so engine.py's spot_change/spot_chg_pct lookup always
        # falls back to 0 when SENSEX is the *active* symbol — same root
        # cause as the ticker-pill issue, just for the primary header value.
        # Patch it here from the BSE quote already fetched above (sensex_quote)
        # instead of touching engine.py's NSE-oriented lookup.
        if SYMBOL == "SENSEX" and sensex_quote:
            if sensex_quote.get("Change") is not None:
                ctx_dict["spot_change"] = sensex_quote["Change"]
            if sensex_quote.get("% Change") is not None:
                ctx_dict["spot_chg_pct"] = sensex_quote["% Change"]

        export_dashboard_json(
            df_clean=df_clean, master=engine_result.master, ctx_dict=ctx_dict,
            SYMBOL=SYMBOL, EXPIRY=EXPIRY, dte=dte, engine_result=engine_result,
            out_path="mTerminals.json", expiry_dates=expiry_dates, extra_chains=extra_chains if extra_chains else None,
            use_virtual_oi=not NO_VIRTUAL_OI, contributors=contributors, all_indices=all_indices
        )
        print("\nSUCCESS: JSON Framework updated snapshot successfully.")

    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    if LOOP_INTERVAL > 0:
        print(f"[Loop] Active monitoring interval: {LOOP_INTERVAL} min. Use Ctrl+C to terminate.\n")
        while True:
            main()
            time.sleep(LOOP_INTERVAL * 60)
    else:
        main()
