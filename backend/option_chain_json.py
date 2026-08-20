import argparse
import logging
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd

from config import settings as _pipeline_settings
from engine import build_engine_result
from expiry_manager import (
    BSE_EXPIRY_DEFAULT,
    _generate_bse_expiry_series,
    _nearest_Thursday,
    _nearest_Tuesday,
    make_expiry_manager,
)
from index_contributors import SYMBOL_TO_INDEX_BASKET, _compute_index_contributors
from lot_sizes import LOT_SIZES

# Authenticated broker adapters are imported lazily only when
# USE_SMARTAPI is true. The separate unauthenticated daily instrument
# master remains available through the broker instrument helpers for lot
# sizes/reference metadata without initializing a login session.
from market_api import (
    BSE_INDEX_SCRIP_CODES,
    fetch_all_indices,
    fetch_bse_index_quote,
    fetch_bse_json_options,
    fetch_option_chain,
    fetch_public_futures,
    get_unified_market_data,
    parse_option_chain_response,
)
from oi.oi_analysis import (
    append_json_history,
    build_oi_history,
    compute_dte,
    read_last_json_snapshot,
)
from pipeline_config import RuntimeConfig
from storage.caches import TTLSlot

logger = logging.getLogger(__name__)

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
_DF_IDX_CACHE = TTLSlot(ttl_seconds=DF_IDX_TTL_SECONDS, clock="epoch")
_DF_IDX_REFRESH_LOCK = threading.Lock()
_DF_IDX_REFRESHING = False


def _refresh_df_idx_background():
    """Runs off the tick's critical path — see _fetch_all_indices_cached."""
    global _DF_IDX_REFRESHING
    try:
        _DF_IDX_CACHE.set(fetch_all_indices())
    except Exception as e:
        logger.error(f"[_refresh_df_idx_background] fetch_all_indices failed: {e}")
    finally:
        with _DF_IDX_REFRESH_LOCK:
            _DF_IDX_REFRESHING = False


def _fetch_all_indices_cached():
    """Stale-while-revalidate: on TTL expiry, kick off the 6-way NSE
    refresh in a background thread and return the last known value
    immediately, instead of blocking this tick's pipeline on it. Only
    the very first call (cold start, no value yet) blocks, since there's
    nothing to fall back to.
    """
    global _DF_IDX_REFRESHING

    if _DF_IDX_CACHE.value is None:
        # Cold start — nothing cached yet, must block once.
        _DF_IDX_CACHE.set(fetch_all_indices())
        return _DF_IDX_CACHE.value

    if not _DF_IDX_CACHE.is_fresh():
        with _DF_IDX_REFRESH_LOCK:
            already_refreshing = _DF_IDX_REFRESHING
            if not already_refreshing:
                _DF_IDX_REFRESHING = True
        if not already_refreshing:
            threading.Thread(
                target=_refresh_df_idx_background,
                daemon=True,
                name="df_idx_refresh",
            ).start()

    return _DF_IDX_CACHE.value


# ─── Virtual OI estimator coordinator loader ──────────────────────────
# Import conditionally to avoid loading models when --no-virtual-oi is set
load_virtual_oi_coordinator = None

# =====================================================================
# NSE/BSE Option Chain Downloader (JSON Only Pipeline)
# =====================================================================

# BSE expiry-date generation (_nearest_weekday/_nearest_Tuesday/
# _nearest_Thursday/_nearest_monthly_thursday/BSE_EXPIRY_DEFAULT/
# _generate_bse_expiry_series/BSE_SCRIP_CD) — moved to expiry_manager.py
# (Step 5a of the v4 migration plan; see the import near the top of this
# file).


_parser = argparse.ArgumentParser(prog="option_chain_json", add_help=True)
_parser.add_argument("--exchange", default="NSE", choices=["NSE", "BSE"])
_parser.add_argument("--symbol", default="NIFTY")
_parser.add_argument("--interval", default=0, type=int)
_parser.add_argument(
    "--no-extra-chains",
    action="store_true",
    help="Disable multi-expiry chains for faster performance",
)
_parser.add_argument(
    "--strict-expiry",
    action="store_true",
    help="Don't auto-resolve to different expiry if requested expiry has no data",
)
_parser.add_argument(
    "--no-virtual-oi",
    action="store_true",
    help="Disable VirtualOI model inference for faster performance",
)

_pre, _ = _parser.parse_known_args()
_sym = (_pre.symbol or "NIFTY").strip().upper()
_default_expiry = (
    BSE_EXPIRY_DEFAULT.get(_sym, _nearest_Thursday)()
    if _sym in {"SENSEX", "BANKEX", "SENSEX50"}
    else _nearest_Tuesday()
)

_parser.add_argument("--expiry", default=_default_expiry, help="Expiry DD-Mmm-YYYY")
_args, _unknown = _parser.parse_known_args()

EXCHANGE = _args.exchange.strip().upper()
SYMBOL = _args.symbol.strip().upper()
EXPIRY = _args.expiry.strip()
LOOP_INTERVAL = _args.interval
NO_EXTRA_CHAINS = _args.no_extra_chains
STRICT_EXPIRY = _args.strict_expiry
NO_VIRTUAL_OI = _args.no_virtual_oi

# How many strikes each side of ATM the engine computes Greeks/OI-velocity/
# signal analytics for. Standalone default is 10; a long-lived host
# (ws_server_live.py) repoints this via set_runtime_config() below, based on
# its own --no-smartapi / --strikes-each-side. Both call sites below must
# read this module-level name at call time (not bake in a literal default)
# or that override is a no-op — which was the bug: strikes stayed pinned at
# 10 even under --no-smartapi, where 50 was intended.
STRIKES_EACH_SIDE = 15

# Underlying price source fed into df["Spot"] (and downstream into every
# engine.py bs_* Greeks call, wall selection, PCR, etc.):
#   "EQ"  (default) — NSE option-chain response's own underlyingValue,
#         a cash-market index quote. Reliable all session, EXCEPT it goes
#         stale in roughly the last 15 min before close (~3:15-3:30) —
#         NSE's own closing-auction/index-computation window means this
#         field stops updating even though the market (and the F&O chain
#         itself) is still live and moving. With EQ frozen, every score
#         downstream (PCR, walls, confidence, verdicts) reads a fixed
#         spot and produces nothing new to evaluate — "everything goes
#         blind" for that last stretch.
#   "FUT" — near-month futures LTP (already fetched every tick via
#         fetch_futures_wide() into df_fut, previously computed and
#         discarded). Futures keep trading and ticking live through
#         3:30, so swapping this in during the EQ-stale window keeps the
#         whole pipeline evaluating instead of freezing. Not basis-
#         adjusted back toward EQ's frame — during the window this
#         matters most, EQ isn't a trustworthy reference to adjust
#         toward anyway, and using the futures price directly is the
#         standard convention for a live options-desk reference price
#         near close.
# Manual only (no auto-fallback) — see set_price_source()/pipeline_config.py.
PRICE_SOURCE = "EQ"

# Which monthly futures contract PRICE_SOURCE="FUT" resolves to —
# "NEAR" (current month, default), "NEXT", or "FAR". See
# fetch_futures_wide()'s docstring (smartapi_pipeline_adapter.py): this
# used to silently pass the options chain's own EXPIRY (often weekly)
# as the futures expiry filter, which only ever matched on the monthly
# expiry week and returned an empty futures fetch every other week —
# fixed by resolving futures by relative monthly position instead of
# reusing the options expiry string. Manual only, via set_runtime_config().
FUTURES_EXPIRY = "NEAR"

# Whether the base option-chain fetch itself uses SmartAPI REST or falls
# back to market_api.py's NSE/BSE-native REST. Standalone default is True;
# a long-lived host repoints this via set_runtime_config() below, based on
# --no-smartapi. Previously --no-smartapi only disabled the websocket
# overlay and index_quote_loop's batched ticker quotes — _fetch_and_parse()
# below still called fetch_option_chain_wide() (SmartAPI REST)
# unconditionally every POLL_SECONDS tick from startup, which is what
# tripped Angel's getMarketData rate limit even with --no-smartapi set.
# This flag closes that gap.
# NOTE: SmartAPI is enabled by default unless the user explicitly
# disables it via CLI/env. MARKET_DATA_PROVIDER selects the REST quote
# adapter, not whether the authenticated SmartAPI chain pipeline is on.
# This keeps Upstox/Shoonya websocket provider selection independent from
# the SmartAPI REST chain path.
USE_SMARTAPI = os.environ.get("MTERMINALS_NO_SMARTAPI") != "1"


def set_runtime_config(cfg: RuntimeConfig) -> None:
    """External hook for a long-lived host (ws_server_live.py) to repoint
    this module's per-tick runtime config between pipeline calls. Replaces
    the previous pattern of poking module attributes directly by name
    (option_chain_json.SYMBOL = ..., option_chain_json.STRIKES_EACH_SIDE =
    ...) from two separate call sites in ws_server_live.py.

    Only overwrites fields that are explicitly set (non-None) on `cfg` —
    matches the previous poke-by-attribute semantics 1:1, so a caller can
    still update e.g. just strikes_each_side without disturbing SYMBOL/
    EXPIRY/etc.

    Standalone runs (`python option_chain_json.py --symbol ...`) never
    call this — they use the argparse-derived defaults set above and
    never touch it again.

    NOTE: there is no `exchange` field. See pipeline_config.py's module
    docstring — an external EXCHANGE override was already inert before
    this refactor (main() always recomputes it locally from SYMBOL), so
    it's not part of this contract.
    """
    global SYMBOL, EXPIRY, NO_EXTRA_CHAINS, STRICT_EXPIRY, NO_VIRTUAL_OI
    global STRIKES_EACH_SIDE, USE_SMARTAPI, PRICE_SOURCE, FUTURES_EXPIRY
    if cfg.symbol is not None:
        SYMBOL = cfg.symbol
    if cfg.expiry is not None:
        EXPIRY = cfg.expiry
    if cfg.no_extra_chains is not None:
        NO_EXTRA_CHAINS = cfg.no_extra_chains
    if cfg.strict_expiry is not None:
        STRICT_EXPIRY = cfg.strict_expiry
    if cfg.no_virtual_oi is not None:
        NO_VIRTUAL_OI = cfg.no_virtual_oi
    if cfg.strikes_each_side is not None:
        STRIKES_EACH_SIDE = cfg.strikes_each_side
    if cfg.use_smartapi is not None:
        USE_SMARTAPI = cfg.use_smartapi
    if cfg.price_source is not None:
        src = cfg.price_source.strip().upper()
        if src not in ("EQ", "FUT"):
            raise ValueError(
                f"price_source must be 'EQ' or 'FUT', got {cfg.price_source!r}"
            )
        PRICE_SOURCE = src
    if cfg.futures_expiry is not None:
        fexp = cfg.futures_expiry.strip().upper()
        if fexp not in ("NEAR", "NEXT", "FAR"):
            raise ValueError(
                f"futures_expiry must be 'NEAR', 'NEXT', or 'FAR', got {cfg.futures_expiry!r}"
            )
        FUTURES_EXPIRY = fexp


logger.info("=== LIGHTWEIGHT JSON OPTIONS PIPELINE INITIALIZATION ===")
logger.info(f"    Exchange: {EXCHANGE} | Symbol: {SYMBOL} | Expiry: {EXPIRY}")
logger.info(
    f"    Loop    : {'every ' + str(LOOP_INTERVAL) + ' min' if LOOP_INTERVAL > 0 else 'single run'}\n"
)

# =====================================================================
# FETCH, PARSE & STRUCTURING
# =====================================================================


def _fetch_bse_chain_no_smartapi(symbol, expiry_dash):
    """market_api.fetch_bse_json_options() normalised to match
    fetch_option_chain_wide()'s column schema. Its raw columns differ
    (Strike vs StrikePrice, no Expiry/Spot/Symbol, no *_PctChgOI/*_pChange/
    *_BuyQty/*_SellQty) — this fills the gap. PctChgOI/pChange/BuyQty/
    SellQty aren't available from this BSE endpoint at all, so they're
    zeroed rather than guessed; anything downstream reading those fields
    for SENSEX/BANKEX under --no-smartapi will see 0, not real data.
    """
    scrip_cd = BSE_INDEX_SCRIP_CODES.get(symbol.upper())
    if not scrip_cd:
        logger.error("No public BSE derivative code for %s", symbol)
        return pd.DataFrame()
    expiry_bse = expiry_dash.replace("-", " ")  # "02-Jul-2026" -> "02 Jul 2026"
    df, spot = fetch_bse_json_options(expiry_bse, scrip_cd=scrip_cd)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"Strike": "StrikePrice"})
    df["Expiry"] = expiry_dash
    df["Spot"] = spot
    df["Symbol"] = symbol
    for side in ("CE", "PE"):
        for col in (
            f"{side}_PctChgOI",
            f"{side}_pChange",
            f"{side}_BuyQty",
            f"{side}_SellQty",
        ):
            if col not in df.columns:
                df[col] = 0
    return df


def _canon_symbol(symbol):
    """Map a full-company-name symbol to the exchange ticker once, so every
    downstream consumer uses one consistent key: the chain DataFrame's Symbol
    column, _day_open_oi()/NSE-anchor keys, LOT_SIZES lookups and
    build_engine_result()'s own symbol filter must all agree or the OI /
    ChgOI / lot-size scaling silently diverge. Idempotent for tickers.
    No-op (returns the raw upper symbol) in no-smartapi mode."""
    raw = (symbol or "").strip().upper()
    if not USE_SMARTAPI or not raw:
        return raw
    try:
        from smartapi_pipeline_adapter import _canon_underlying

        return _canon_underlying(raw)
    except Exception:
        return raw


def _fetch_and_parse(symbol, expiry, exchange, strict_expiry=False):
    symbol = _canon_symbol(symbol)
    if USE_SMARTAPI:
        from smartapi_pipeline_adapter import (
            fetch_option_chain_wide,
            get_available_expiries,
        )
    if exchange == "BSE":
        # Was fetch_bse_json_options() — BSE's own JSON option-chain HTTP
        # endpoint. fetch_option_chain_wide() is exchange-parametrized
        # (STRIKE_INTERVALS/_get_strike_interval already cover SENSEX/
        # BANKEX/SENSEX50), so exchange="BFO" is a genuine drop-in: same
        # output columns (StrikePrice/Expiry/Spot/Symbol/CE_*/PE_*) this
        # branch used to hand-build via the Strike->StrikePrice rename
        # below, now produced natively.
        if USE_SMARTAPI:
            df = fetch_option_chain_wide(symbol, expiry, exchange="BFO")
            if df.empty:
                raise RuntimeError(
                    f"SmartAPI BFO chain fetch empty for {symbol} {expiry}"
                )
        else:
            # NSE-native path never covers BSE, so this is market_api.py's
            # own BSE scrape, normalised to the same column schema
            # fetch_option_chain_wide() produces (StrikePrice/Expiry/Spot/
            # Symbol/CE_*/PE_*) so downstream (build_master_table_nse etc.)
            # doesn't need to know which source it came from.
            df = _fetch_bse_chain_no_smartapi(symbol, expiry)
            if df.empty:
                raise RuntimeError(
                    f"NSE-fallback BFO chain fetch empty for {symbol} {expiry}"
                )
        spot = df["Spot"].iloc[0] if "Spot" in df.columns else 0.0
        expiry_dates = _generate_bse_expiry_series(symbol)
        return df, spot, expiry_dates
    else:
        # When using NSE API (no-smartapi mode), get expiries from NSE itself
        # instead of SmartAPI ScripMaster to avoid format mismatches
        if USE_SMARTAPI:
            from brokers.market_data import get_active_provider

            provider = get_active_provider()

            if provider in ("UPSTOX", "SHOONYA", "KITE", "BREEZE", "KOTAK"):
                from brokers.market_data import market_data

                try:
                    provider_expiries = market_data.list_expiries(
                        symbol,
                        exchange=("BFO" if exchange == "BSE" else "NFO"),
                    )

                    expiry_dates = [
                        pd.to_datetime(e, format="%d%b%Y").strftime("%d-%b-%Y")
                        for e in provider_expiries
                    ]
                except Exception as exc:
                    logger.warning(
                        "[Expiry] %s expiry lookup failed for %s: %s",
                        provider,
                        symbol,
                        exc,
                    )
                    expiry_dates = []

                # Provider authentication/search failure must not kill
                # the option-chain pipeline. ScripMaster is already loaded.
                if not expiry_dates:
                    logger.warning(
                        "[Expiry] %s returned no expiries for %s; "
                        "falling back to ScripMaster",
                        provider,
                        symbol,
                    )
                    expiry_dates = get_available_expiries(symbol)

            else:
                expiry_dates = get_available_expiries(symbol)
            resolved = expiry

            # Compare by parsed calendar date, not raw string equality — the
            # frontend's expiry string ("28-Jul-2026", from payload.expiryDates)
            # and get_available_expiries(symbol)'s own strings can be the exact
            # same date in a different case/format, and a raw `in` check here
            # used to silently treat that as "not found" and fall through to
            # future[0] (nearest future expiry) every time, even when the
            # requested expiry was genuinely available. Swap in the matched
            # CANONICAL string from expiry_dates (not the raw input) so
            # downstream fetch_option_chain_wide()/fetch_option_chain() calls,
            # which may do their own exact-string lookups, get a string they
            # actually recognize too.
            def _expiry_date(s):
                try:
                    return pd.to_datetime(s, format="%d-%b-%Y").date()
                except (ValueError, TypeError):
                    return None

            target_date = _expiry_date(resolved)
            matched = next(
                (
                    e
                    for e in expiry_dates
                    if target_date is not None and _expiry_date(e) == target_date
                ),
                None,
            )
            if matched is not None:
                resolved = matched
            else:
                if strict_expiry:
                    raise RuntimeError(
                        f"requested expiry {expiry!r} not available for "
                        f"{symbol!r} (offered: {expiry_dates})"
                    )
                today = date.today()
                future = [
                    e
                    for e in expiry_dates
                    if pd.to_datetime(e, format="%d-%b-%Y").date() >= today
                ]
                if not future:
                    raise RuntimeError(
                        f"no future expiries available for {symbol!r} "
                        f"(offered: {expiry_dates})"
                    )
                resolved = future[0]
                logger.info(f"[Expiry] '{expiry}' unavailable → selected: '{resolved}'")
            df = fetch_option_chain_wide(
                symbol, resolved, strikes_around_atm=STRIKES_EACH_SIDE
            )
        else:
            # NSE mode: get expiries directly from NSE API response
            payload = fetch_option_chain(symbol, expiry)
            if not payload:
                raise RuntimeError(f"NSE API returned no data for {symbol}")
            expiry_dates = payload.get("records", {}).get("expiryDates", [])
            resolved = _resolve_expiry(payload, expiry, strict=strict_expiry)
            # Re-fetch with the resolved expiry to ensure consistency
            if resolved != expiry:
                payload = fetch_option_chain(symbol, resolved)
            df = parse_option_chain_response(payload, resolved)

        if df.empty:
            raise RuntimeError(
                f"{'SmartAPI' if USE_SMARTAPI else 'NSE'} chain fetch empty for {symbol} {resolved}"
            )
        spot = df["Spot"].iloc[0] if "Spot" in df.columns else 0.0
        return df, spot, resolved, expiry_dates


def _resolve_expiry(data, requested_expiry, strict=False):
    available = data["records"].get("expiryDates", [])
    if requested_expiry and data["records"].get("data", []):
        return requested_expiry
    if strict:
        raise RuntimeError(
            f"Requested expiry '{requested_expiry}' has no data. Available: {available}"
        )
    today = date.today()
    for exp in available:
        try:
            if pd.to_datetime(exp, format="%d-%b-%Y").date() >= today:
                logger.info(f"[Expiry] '{requested_expiry}' empty → selected: '{exp}'")
                return exp
        except Exception:
            continue
    raise RuntimeError(f"No valid future expiry found: {available}")


# Lot-size resolution (_STATIC_LOT_SIZES/_LiveLotSizes/LOT_SIZES) — moved
# to lot_sizes.py (Step 5b of the v4 migration plan; see the import near
# the top of this file).

# Index contributors (SYMBOL_TO_INDEX_BASKET/_compute_index_contributors)
# — moved to index_contributors.py (Step 5c of the v4 migration plan; see
# the import near the top of this file).


def _build_expiry_bundle(
    symbol, expiry, exchange="NSE", strict_expiry=False, **engine_kwargs
):
    symbol = _canon_symbol(symbol)
    if exchange == "BSE":
        df, spot, _ = _fetch_and_parse(symbol, expiry, exchange, strict_expiry)
        resolved = expiry
    else:
        df, spot, resolved, _ = _fetch_and_parse(
            symbol, expiry, exchange, strict_expiry
        )

    df_clean = (
        df.dropna(subset=["StrikePrice"])
        .drop_duplicates(subset=["StrikePrice"])
        .sort_values("StrikePrice")
        .copy()
    )
    dte = compute_dte(resolved)

    engine_kwargs.pop(
        "velocity_window_minutes", None
    )  # dead param, removed from build_engine_result; discarded here so it can't leak through **engine_kwargs below
    engine_result = build_engine_result(
        df=df,
        df_clean=df_clean,
        df_idx=None,
        df_fut=None,
        df_full_history=None,
        symbol=symbol,
        expiry=resolved,
        dte=dte,
        lot_size=engine_kwargs.pop("lot_size", LOT_SIZES.get(symbol, 65)),
        n_strikes_each_side=engine_kwargs.pop("n_strikes_each_side", STRIKES_EACH_SIDE),
        **engine_kwargs,
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
        if USE_SMARTAPI:
            from smartapi_pipeline_adapter import (
                fetch_all_pills_and_vix_batched,
                fetch_futures_wide,
                fetch_sensex_ticker_smartapi,
                fetch_ticker_payload_smartapi,
                fetch_vix_smartapi,
            )
        # ── Fetch chain + futures + all-indices + VIX + ticker pills concurrently ──
        # These five NSE/BSE calls are independent of each other (futures/
        # indices/VIX/ticker-pills don't need the option-chain result), so
        # running them one after another was pure serial waiting. This was
        # the single biggest contributor to per-tick latency. (Used to be six
        # — a separate fetch_india_vix() call was folded into
        # get_unified_market_data() below, removing a redundant NSE round-trip.)
        with ThreadPoolExecutor(max_workers=5) as ex:
            if EXCHANGE == "BSE":
                fut_chain = ex.submit(
                    _fetch_and_parse, SYMBOL, EXPIRY, "BSE", STRICT_EXPIRY
                )
                # Was fetch_bse_futures() (BSE HTTP + scrip code lookup).
                # fetch_futures_wide() already resolves FUTIDX contracts
                # generically off the ScripMaster (_get_futures_contract),
                # same as the NSE branch below — exchange="BFO" is the only
                # difference. expiry_dash left None (was: EXPIRY) — see
                # fetch_futures_wide()'s docstring for why reusing the
                # options chain's own expiry here was wrong; `which`
                # (FUTURES_EXPIRY) picks the monthly contract instead.
                fut_fut = (
                    ex.submit(
                        fetch_futures_wide,
                        SYMBOL,
                        None,
                        exchange="BFO",
                        which=FUTURES_EXPIRY,
                    )
                    if USE_SMARTAPI
                    else ex.submit(fetch_public_futures, SYMBOL, FUTURES_EXPIRY)
                )
            else:
                fut_chain = ex.submit(
                    _fetch_and_parse, SYMBOL, EXPIRY, "NSE", STRICT_EXPIRY
                )
                fut_fut = (
                    ex.submit(fetch_futures_wide, SYMBOL, None, which=FUTURES_EXPIRY)
                    if USE_SMARTAPI
                    else ex.submit(fetch_public_futures, SYMBOL, FUTURES_EXPIRY)
                )
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
            fut_batch = (
                ex.submit(fetch_all_pills_and_vix_batched) if USE_SMARTAPI else None
            )
            if fut_batch is not None:
                fut_batch.result()
            # In broker mode, ticker/VIX/SENSEX use SmartAPI. Under
            # --no-smartapi these futures stay absent and the public
            # NSE/BSE fallback below derives the same display payload.
            fut_ticker = (
                ex.submit(fetch_ticker_payload_smartapi) if USE_SMARTAPI else None
            )
            fut_unified = ex.submit(fetch_vix_smartapi) if USE_SMARTAPI else None
            fut_sensex = (
                ex.submit(fetch_sensex_ticker_smartapi) if USE_SMARTAPI else None
            )
            fut_public_bse_quotes = (
                {
                    sym: ex.submit(fetch_bse_index_quote, sym)
                    for sym in BSE_INDEX_SCRIP_CODES
                }
                if not USE_SMARTAPI
                else {}
            )

            if EXCHANGE == "BSE":
                df, spot, expiry_dates = fut_chain.result()
            else:
                df, spot, resolved, expiry_dates = fut_chain.result()
                if resolved != EXPIRY:
                    EXPIRY = resolved
            df_fut = fut_fut.result()

            # Broker-neutral market_data adapters may return a quote dict.
            # The analytics engine expects a DataFrame.
            if isinstance(df_fut, dict):
                df_fut = pd.DataFrame([df_fut])
            elif df_fut is None:
                df_fut = pd.DataFrame()
            df_idx = fut_idx.result()

            # EQ remains the canonical option-pricing and decision reference.
            # Futures are passed separately into build_engine_result() for
            # basis/regime confirmation and never replace df["Spot"].

            if USE_SMARTAPI:
                _live_vix, _live_vix_chg_pct = fut_unified.result()
                sensex_quote = fut_sensex.result()
                ticker_payload = fut_ticker.result()
            else:
                # Strict broker-free path: derive NSE ticker pills from the
                # already-fetched index frame and obtain VIX from NSE. BSE's
                # public endpoint supplies SENSEX without an AngelOne login.
                _live_vix, _live_vix_chg_pct, ticker_payload = get_unified_market_data(
                    df_idx
                )
                bse_quotes = [
                    future.result() for future in fut_public_bse_quotes.values()
                ]
                bse_quotes = [quote for quote in bse_quotes if quote]
                sensex_quote = next(
                    (quote for quote in bse_quotes if quote.get("Symbol") == "SENSEX"),
                    None,
                )
            _live_vix = _live_vix or 0.0
            all_indices = list(ticker_payload)
            if sensex_quote:
                all_indices.append(sensex_quote)
            if not USE_SMARTAPI:
                all_indices.extend(
                    quote for quote in bse_quotes if quote.get("Symbol") != "SENSEX"
                )

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
            logger.error("Error: Invalid Spot Price. Core calculations aborted.")
            return

        # Derived from df_idx (already fetched above, no new network call) —
        # empty list for symbols with no matching NSE index basket (BSE, etc.)
        contributors = _compute_index_contributors(df_idx, SYMBOL, spot)

        dte = compute_dte(EXPIRY)
        df_clean = (
            df.dropna(subset=["StrikePrice"])
            .drop_duplicates(subset=["StrikePrice"])
            .sort_values("StrikePrice")
            .copy()
        )

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
                logger.warning(f"[ExpiryManager] Context skip ({e})")

        # Extra chains management — NEAR and MONTHLY are independent of each
        # other, so build them concurrently instead of one after another.
        extra_chains = {}
        if not NO_EXTRA_CHAINS and em is not None:
            try:
                slots = [
                    (slot_name, slot)
                    for slot_name, slot in [
                        ("NEAR", em.context.near),
                        ("MONTHLY", em.context.monthly),
                    ]
                    if slot and slot.date_str != str(EXPIRY)
                ]
                if slots:
                    with ThreadPoolExecutor(max_workers=len(slots)) as ex2:
                        futs = {
                            ex2.submit(
                                _build_expiry_bundle, SYMBOL, slot.date_str, EXCHANGE
                            ): (slot_name, slot)
                            for slot_name, slot in slots
                        }
                        for f in as_completed(futs):
                            slot_name, slot = futs[f]
                            try:
                                n_df, n_master, n_ctx, n_dte, _ = f.result()
                                extra_chains[slot.date_str] = (
                                    n_df,
                                    n_master,
                                    n_ctx,
                                    n_dte,
                                )
                            except Exception as e:
                                logger.warning(f"[{slot_name}] Skip extra bundle ({e})")
            except Exception as e:
                logger.warning(f"[ExtraChains] Skip ({e})")

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
        _far_expiry_str = (
            em.context.monthly.date_str
            if em is not None and em.context.monthly
            else em.context.far.date_str
            if em is not None and em.context.far
            else ""
        )

        engine_result = build_engine_result(
            df=df,
            df_clean=df_clean,
            df_idx=df_idx,
            df_fut=df_fut,
            df_full_history=history_df,
            symbol=SYMBOL,
            expiry=EXPIRY,
            dte=dte,
            lot_size=LOT_SIZES.get(SYMBOL, 65),
            n_strikes_each_side=STRIKES_EACH_SIDE,
            india_vix=_live_vix,
            india_vix_chg_pct=_live_vix_chg_pct,
            near_expiry=_near_expiry_str,
            far_expiry=_far_expiry_str,
        )

        from mTerminals_json import export_dashboard_json

        ctx_dict = engine_result.to_ctx_dict()

        # SENSEX never appears in df_idx (fetch_all_indices()/DEFAULT_INDICES
        # is NSE-only), so engine.py's spot_change/spot_chg_pct lookup always
        # falls back to 0 when SENSEX is the *active* symbol — same root
        # cause as the ticker-pill issue, just for the primary header value.
        # Patch it here from the BSE quote already fetched above (sensex_quote)
        # instead of touching engine.py's NSE-oriented lookup.
        active_bse_quote = (
            next(
                (quote for quote in all_indices if quote.get("Symbol") == SYMBOL), None
            )
            if SYMBOL in _BSE_SYMBOLS
            else None
        )
        if active_bse_quote:
            if active_bse_quote.get("Change") is not None:
                ctx_dict["spot_change"] = active_bse_quote["Change"]
            if active_bse_quote.get("% Change") is not None:
                ctx_dict["spot_chg_pct"] = active_bse_quote["% Change"]

        export_dashboard_json(
            df_clean=df_clean,
            master=engine_result.master,
            ctx_dict=ctx_dict,
            SYMBOL=SYMBOL,
            EXPIRY=EXPIRY,
            dte=dte,
            engine_result=engine_result,
            out_path="mTerminals.json",
            expiry_dates=expiry_dates,
            extra_chains=extra_chains if extra_chains else None,
            use_virtual_oi=not NO_VIRTUAL_OI,
            contributors=contributors,
            all_indices=all_indices,
            price_source=PRICE_SOURCE,
            futures_expiry=FUTURES_EXPIRY,
        )
        print()
        logger.info("SUCCESS: JSON Framework updated snapshot successfully.")

    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    if LOOP_INTERVAL > 0:
        logger.info(
            f"[Loop] Active monitoring interval: {LOOP_INTERVAL} min. Use Ctrl+C to terminate.\n"
        )
        while True:
            main()
            time.sleep(LOOP_INTERVAL * 60)
    else:
        main()
