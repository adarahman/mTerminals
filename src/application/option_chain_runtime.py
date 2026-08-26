"""Lightweight JSON options pipeline (refactored).

Behavior-preserving refactor of the former import-time-heavy module:

- CLI arguments are parsed ONLY when run as a script (__main__), never at
  import. Hosts pass one RuntimeConfig directly to main().
- main() is decomposed into stage helpers (_gather_market_data,
  _merge_volume_value, _build_extra_chains, ...) — each independently
  readable/testable; control flow and ordering inside each stage match the
  previous monolith exactly.
- The expiry-manager compatibility re-exports remain available
  (BSE_EXPIRY_DEFAULT/_nearest_Thursday/_nearest_Tuesday/
  _generate_bse_expiry_series) that ws_server_live reads THROUGH this
  module.

Pipeline helpers consume the explicit pass-scoped RuntimeConfig.
"""

import argparse
import atexit
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
    as_completed,
)
from datetime import date, datetime, time as dtime

import pandas as pd

from decision.engine import build_engine_result
from market.expiry.service import (
    BSE_EXPIRY_DEFAULT,
    _generate_bse_expiry_series,
    _nearest_Thursday,
    _nearest_Tuesday,
    make_expiry_manager,
)
from analytics.index_contributors import (
    SYMBOL_TO_INDEX_BASKET,
    _compute_index_contributors,
)
from market.instruments.lot_sizes import LOT_SIZES

from market.providers.option_chain import PublicOptionChainAdapter
from oi.oi_analysis import (
    append_json_history,
    build_oi_history,
    compute_dte,
    read_last_json_snapshot,
)
from application.pipeline_config import RuntimeConfig
from market.option_chain.requests import MarketDataRequestPlan
from market.option_chain.gatherer import ConcurrentMarketDataGatherer
from market.option_chain.runtime_adapters import BrokerMarketAdapters
from market.option_chain.service import (
    ExpiryResolutionService,
    OptionChainFetchService,
)
from storage.caches import TTLSlot

logger = logging.getLogger(__name__)

# Expiry-date generation helpers live in expiry_manager.py (Step 5a of the
# v4 migration plan); lot-size resolution in lot_sizes.py (5b); index
# contributors in index_contributors.py (5c). They are re-exported above —
# ws_server_live reads BSE_EXPIRY_DEFAULT/_nearest_* THROUGH this module.

_BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}
_EXPIRY_RESOLVER = ExpiryResolutionService()
_PUBLIC_MARKET = PublicOptionChainAdapter()


def _exchange_for_symbol(symbol: str) -> str:
    return "BSE" if symbol in _BSE_SYMBOLS else "NSE"


# ─── Runtime configuration (module globals, host-mutable) ────────────
# Initialized to host-friendly constants; the standalone CLI overwrites
# them in _apply_cli_overrides() (see __main__). NOTHING here touches
# sys.argv at import time — that was the old behavior and forced every
# importing host to hide argv mid-import.
#
# NOTE: authenticated broker mode is enabled by default unless
# BROKER_SERVICES_ENABLED=false. ``use_smartapi`` is the legacy configuration
# spelling; runtime code uses RuntimeConfig.broker_enabled because the active
# provider may be SmartAPI, Upstox, Shoonya, Kite, Breeze, or Kotak.
try:
    from infrastructure.config import settings as _broker_settings
except ImportError:  # pragma: no cover - standalone legacy invocation
    _broker_settings = None
_DEFAULT_USE_SMARTAPI = (
    _broker_settings.broker_services_enabled
    if _broker_settings is not None
    else True
)

_DEFAULT_RUNTIME_CONFIG = RuntimeConfig(
    symbol="NIFTY",
    expiry=_nearest_Tuesday(),
    no_extra_chains=False,
    strict_expiry=False,
    no_virtual_oi=False,
    strikes_each_side=15,
    use_smartapi=_DEFAULT_USE_SMARTAPI,
    price_source="AUTO",
    futures_expiry="NEAR",
)

# Underlying price source fed into df["Spot"] (and downstream into every
# engine.py bs_* Greeks call, wall selection, PCR, etc.):
#   "AUTO" (default) — keep the option-chain EQ spot while it is healthy,
#         but replace it with the active broker's live cash/index quote when
#         EQ is stale. If no live cash quote is available, use the selected
#         futures LTP near/after the close window. This prevents a stale NSE
#         underlyingValue from freezing every downstream analytic.
#   "EQ" — force the option-chain response's underlyingValue.
#   "FUT" — force the selected near-month futures LTP. Futures are not
#         basis-adjusted back toward EQ.
# Runtime-configurable through RuntimeConfig.

# Which monthly futures contract PRICE_SOURCE="FUT" resolves to — "NEAR"
# (current month, default), "NEXT", or "FAR". See fetch_futures_wide()'s
# docstring (broker_pipeline.py): this used to silently reuse the options
# chain's own (often weekly) expiry as the futures filter, matching only on
# the monthly week and returning empty futures every other week. Manual
# only, through RuntimeConfig.


# ─── df_idx TTL cache ────────────────────────────────────────────────
# fetch_all_indices() is the one NSE HTTP call with no SmartAPI equivalent
# — it feeds _compute_index_contributors()'s ffmc weighting AND the
# Volume/Value merge into all_indices. Its consumers don't need per-tick
# freshness (free-float weights and session volume totals barely move
# second to second), so the call is decoupled onto its own TTL, cutting
# real NSE HTTP volume without touching anything downstream (same
# DataFrame, refreshed less often).
DF_IDX_TTL_SECONDS = 20
_DF_IDX_CACHE = TTLSlot(ttl_seconds=DF_IDX_TTL_SECONDS, clock="epoch")
_DF_IDX_REFRESH_LOCK = threading.Lock()
_DF_IDX_REFRESHING = False


def _refresh_df_idx_background():
    """Runs off the tick's critical path — see _fetch_all_indices_cached."""
    global _DF_IDX_REFRESHING
    try:
        _DF_IDX_CACHE.set(_PUBLIC_MARKET.fetch_indices())
    except Exception as e:
        logger.error(f"[_refresh_df_idx_background] fetch_all_indices failed: {e}")
    finally:
        with _DF_IDX_REFRESH_LOCK:
            _DF_IDX_REFRESHING = False


def _fetch_all_indices_cached():
    """Stale-while-revalidate: on TTL expiry, kick off the 6-way NSE refresh
    in a background thread and return the last known value immediately,
    instead of blocking this tick's pipeline on it. Only the very first
    call (cold start, nothing cached yet) blocks."""
    global _DF_IDX_REFRESHING

    if _DF_IDX_CACHE.value is None:
        _DF_IDX_CACHE.set(_PUBLIC_MARKET.fetch_indices())
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


# =====================================================================
# FETCH, PARSE & STRUCTURING
# =====================================================================


def _fetch_bse_chain_no_smartapi(symbol, expiry_dash):
    """Compatibility wrapper for the canonical public BSE adapter."""
    return _PUBLIC_MARKET.fetch_bse_chain(symbol, expiry_dash)


def _canon_symbol(symbol, runtime_config, broker_adapters=None):
    """Map a full-company-name symbol to the exchange ticker once, so every
    downstream consumer uses one consistent key: the chain DataFrame's Symbol
    column, _day_open_oi()/NSE-anchor keys, LOT_SIZES lookups and
    build_engine_result()'s own symbol filter must all agree or OI /
    ChgOI / lot-size scaling silently diverge. Idempotent for tickers.
    No-op in public-only mode."""
    raw = (symbol or "").strip().upper()
    if not runtime_config.broker_enabled or not raw:
        return raw
    if broker_adapters is None:
        raise RuntimeError("broker adapters are required in broker mode")
    return broker_adapters.canonicalize_symbol(raw)


def _fetch_and_parse(
    symbol,
    expiry,
    exchange,
    strict_expiry=False,
    runtime_config=None,
    broker_adapters=None,
):
    runtime_config = runtime_config or _DEFAULT_RUNTIME_CONFIG
    symbol = _canon_symbol(symbol, runtime_config, broker_adapters)

    service = OptionChainFetchService(
        canonicalize_symbol=(
            broker_adapters.canonicalize_symbol
            if runtime_config.broker_enabled
            else lambda value: (value or "").strip().upper()
        ),
        fetch_broker_chain=(
            broker_adapters.fetch_chain
            if runtime_config.broker_enabled
            else lambda *args: None
        ),
        list_broker_expiries=(
            broker_adapters.list_expiries
            if runtime_config.broker_enabled
            else lambda *args: []
        ),
        fetch_public_bse_chain=_fetch_bse_chain_no_smartapi,
        fetch_public_nse_payload=_PUBLIC_MARKET.fetch_nse_payload,
        parse_public_nse_payload=_PUBLIC_MARKET.parse_nse_payload,
        fetch_bse_quote=_PUBLIC_MARKET.fetch_bse_quote,
        generate_bse_expiries=_generate_bse_expiry_series,
        expiry_resolver=_EXPIRY_RESOLVER,
    )
    request = MarketDataRequestPlan(
        symbol=symbol,
        option_expiry=expiry,
        option_exchange=exchange,
        strict_expiry=strict_expiry,
        futures_expiry=runtime_config.futures_expiry,
        broker_enabled=runtime_config.broker_enabled,
    )
    return service.fetch(
        request, strikes_each_side=runtime_config.strikes_each_side
    )


def _resolve_expiry(data, requested_expiry, strict=False):
    resolved = _EXPIRY_RESOLVER.resolve_public_payload(
        data, requested_expiry, strict=strict
    )
    if resolved != requested_expiry:
        logger.info(
            f"[Expiry] '{requested_expiry}' empty → selected: '{resolved}'"
        )
    return resolved


def _build_expiry_bundle(
    symbol,
    expiry,
    exchange="NSE",
    strict_expiry=False,
    runtime_config=None,
    broker_adapters=None,
    **engine_kwargs,
):
    runtime_config = runtime_config or _DEFAULT_RUNTIME_CONFIG
    symbol = _canon_symbol(symbol, runtime_config, broker_adapters)
    if exchange == "BSE":
        df, spot, _ = _fetch_and_parse(
            symbol,
            expiry,
            exchange,
            strict_expiry,
            runtime_config,
            broker_adapters,
        )
        resolved = expiry
    else:
        df, spot, resolved, _ = _fetch_and_parse(
            symbol,
            expiry,
            exchange,
            strict_expiry,
            runtime_config,
            broker_adapters,
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
    )  # dead param; discarded so it can't leak through **engine_kwargs below
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
        n_strikes_each_side=engine_kwargs.pop(
            "n_strikes_each_side", runtime_config.strikes_each_side
        ),
        **engine_kwargs,
    )
    return df_clean, engine_result.master, engine_result.to_ctx_dict(), dte, resolved


# =====================================================================
# PIPELINE STAGES
# =====================================================================


def _select_runtime_spot(df, spot, df_fut, all_indices, runtime_config):
    """Choose the price actually fed into analytics.

    AUTO is intentionally broker-neutral: when a broker cash/index quote is
    available, it is the best freshness check against NSE's option-chain
    underlyingValue. A material mismatch means the NSE field is stale. If no
    live cash quote exists, AUTO falls back to futures during the final cash
    session window — and ONLY that window (15:15-15:30 on an actual trading
    day). EQ/FUT remain explicit force modes.

    Bug fixed here: the FUT fallback used to be `now.time() >= dtime(15,15)`
    with no upper bound and no trading-day check, so once past 15:15 it
    stayed true for the rest of the day, every evening, and every non-
    trading day (weekends/holidays) indefinitely — not just the narrow
    close window the docstring describes. Combined with live_cash
    legitimately being 0 whenever no live feed is connected (market
    closed), AUTO would then permanently select FUT any time the
    dashboard was viewed outside 9:15-15:15, showing the same futures
    price in both the main spot readout and the FUT ticker pill with no
    way to self-correct until a live feed reconnected.
    """
    source = runtime_config.price_source.strip().upper()
    eq = float(spot or 0.0)

    def _live_index_quote(symbol):
        for row in all_indices or []:
            if str(row.get("Symbol", "")).strip().upper() != symbol.upper():
                continue
            for key in ("Last Price", "ltp", "LTP", "last_price"):
                try:
                    value = float(row.get(key))
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    return value
        return 0.0

    def _futures_ltp(frame):
        if frame is None or getattr(frame, "empty", True):
            return 0.0
        row = frame.iloc[0]
        for key in ("LTP", "ltp", "Last Price", "last_price", "lastPrice"):
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0.0

    from nse_eod_fetch import is_trading_day as _is_trading_day

    live_cash = (
        _live_index_quote(runtime_config.symbol)
        if runtime_config.broker_enabled
        else 0.0
    )
    fut_ltp = _futures_ltp(df_fut)
    selected = eq
    used = "EQ"

    if source == "FUT":
        if fut_ltp > 0:
            selected, used = fut_ltp, "FUT"
    elif source == "AUTO":
        # A broker cash/index quote is a direct freshness reference. 0.05%
        # is deliberately wider than ordinary quote noise but far smaller
        # than the stale-vs-live moves seen when NSE's field stops updating.
        if live_cash > 0 and (eq <= 0 or abs(live_cash - eq) / max(eq, 1.0) > 0.0005):
            selected, used = live_cash, "LIVE_EQ"
        elif live_cash > 0 and eq <= 0:
            selected, used = live_cash, "LIVE_EQ"
        elif (
            dtime(15, 15) <= datetime.now().time() <= dtime(15, 30)
            and _is_trading_day(datetime.now())
            and fut_ltp > 0
        ):
            selected, used = fut_ltp, "FUT"

    if selected <= 0:
        raise RuntimeError(
            f"No usable spot price for {runtime_config.symbol}: "
            f"EQ={eq}, FUT={fut_ltp}"
        )

    if used != "EQ":
        df = df.copy()
        df["Spot"] = selected
        logger.warning(
            "[price-source] %s -> %s for %s (EQ=%s, live=%s, FUT=%s)",
            source,
            used,
            runtime_config.symbol,
            eq,
            live_cash or None,
            fut_ltp or None,
        )
    return df, selected, used


def _gather_market_data(exchange, runtime_config, broker_adapters=None, timings=None):
    """Fan out one pass's independent fetches concurrently and assemble the
    market-context pieces. These NSE/BSE calls are independent of each other
    (futures/indices/VIX/ticker-pills don't need the option-chain result);
    running them serially was pure waiting and the single biggest
    contributor to per-tick latency.

    Returns a dict with keys: df, spot, resolved, expiry_dates, df_fut,
    df_idx, india_vix, india_vix_chg_pct, all_indices."""
    request = MarketDataRequestPlan(
        symbol=runtime_config.symbol,
        option_expiry=runtime_config.expiry,
        option_exchange=exchange,
        strict_expiry=runtime_config.strict_expiry,
        futures_expiry=runtime_config.futures_expiry,
        broker_enabled=runtime_config.broker_enabled,
    )
    if request.broker_enabled and broker_adapters is None:
        raise RuntimeError("broker adapters are required in broker mode")

    def fetch_chain(plan):
        return _fetch_and_parse(
            plan.symbol,
            plan.option_expiry,
            plan.option_exchange,
            plan.strict_expiry,
            runtime_config,
            broker_adapters,
        )

    def fetch_futures(plan):
        if not plan.broker_enabled:
            return _PUBLIC_MARKET.fetch_futures(
                plan.symbol, plan.futures_expiry
            )
        return broker_adapters.fetch_futures(
            plan.symbol,
            plan.broker_derivatives_exchange,
            plan.futures_expiry,
        )

    gathered = ConcurrentMarketDataGatherer(
        fetch_chain=fetch_chain,
        fetch_futures=fetch_futures,
        fetch_indices=_fetch_all_indices_cached,
        warm_broker_batch=(
            broker_adapters.warm_batch if request.broker_enabled else None
        ),
        fetch_ticker_payload=(
            broker_adapters.fetch_ticker_payload
            if request.broker_enabled
            else None
        ),
        fetch_vix=(
            broker_adapters.fetch_vix if request.broker_enabled else None
        ),
        fetch_sensex_quote=(
            broker_adapters.fetch_sensex_quote
            if request.broker_enabled
            else None
        ),
        fetch_public_bse_quote=_PUBLIC_MARKET.fetch_bse_quote,
        public_bse_symbols=_PUBLIC_MARKET.bse_symbols,
        executor=_get_market_io_executor(),
    ).gather(request, timings=timings)

    if request.option_exchange == "BSE":
        df, spot, expiry_dates = gathered.chain
        resolved = request.option_expiry
    else:
        df, spot, resolved, expiry_dates = gathered.chain

    df_fut = gathered.futures
    if isinstance(df_fut, dict):
        df_fut = pd.DataFrame([df_fut])
    elif df_fut is None:
        df_fut = pd.DataFrame()
    df_idx = gathered.indices

    if request.broker_enabled:
        _live_vix, _live_vix_chg_pct = gathered.vix
        sensex_quote = gathered.sensex_quote
        ticker_payload = gathered.ticker_payload
        bse_quotes = []
    else:
        _live_vix, _live_vix_chg_pct, ticker_payload = (
            _PUBLIC_MARKET.unified_market_data(df_idx)
        )
        bse_quotes = [quote for quote in gathered.public_bse_quotes if quote]
        sensex_quote = next(
            (quote for quote in bse_quotes if quote.get("Symbol") == "SENSEX"),
            None,
        )

    _live_vix = _live_vix or 0.0
    all_indices = list(ticker_payload)
    if sensex_quote:
        all_indices.append(sensex_quote)
    if not request.broker_enabled:
        all_indices.extend(
            quote for quote in bse_quotes if quote.get("Symbol") != "SENSEX"
        )

    _merge_volume_value(all_indices, df_idx)
    df, spot, price_source_used = _select_runtime_spot(
        df, spot, df_fut, all_indices, runtime_config
    )

    return {
        "df": df,
        "spot": spot,
        "price_source_used": price_source_used,
        "resolved": resolved,
        "expiry_dates": expiry_dates,
        "df_fut": df_fut,
        "df_idx": df_idx,
        "india_vix": _live_vix,
        "india_vix_chg_pct": _live_vix_chg_pct,
        "all_indices": all_indices,
    }


def _merge_volume_value(all_indices, df_idx):
    """Merge real Volume/Value from df_idx (already fetched — no new network
    call). get_unified_market_data()'s /api/allIndices source reports
    Volume/Value hardcoded to 0 on index rows (an index isn't itself traded),
    but df_idx comes from equity-stock-indices, which includes each index's
    own aggregate row with real session-cumulative totals (the numbers NSE's
    live-market page shows). Matched on Symbol — already the same
    INDEX_RENAME'd string on both sides. Frontend: dashboard.js's price
    chart reads Value/Volume off allIndices to compute a running VWAP."""
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


def _make_expiry_manager_or_none(expiry_dates):
    """Build the ExpiryManager unconditionally (pure computation off the
    already-fetched expiry_dates, no network cost). Previously it was only
    built inside the extra-chains block, so its verified NEAR/MONTHLY dates
    never reached build_engine_result() — calendar-spread legs fell back to
    placeholder labels and the frontend guessed dates from array position,
    which can land on a stale/wrong entry depending on raw order."""
    em = None
    if expiry_dates:
        try:
            em = make_expiry_manager(expiry_dates)
        except Exception as e:
            logger.warning(f"[ExpiryManager] Context skip ({e})")
    return em


# Extra-expiry analytics are expensive (full chain fetch + engine). Cache each
# rebuilt bundle per (symbol, slot, expiry date) and reuse it until it goes
# stale or the symbol/expiry rolls, so a 6-10s poll does not re-run the entire
# analytics engine for NEAR/MONTHLY every single tick.
_EXTRA_CHAIN_CACHE_TTL_SECONDS = 45.0
_EXTRA_CHAIN_OPERATION_TIMEOUT_SECONDS = 15.0
_extra_chain_cache: dict = {}


# One process-level I/O executor reused across polls instead of spinning up a
# fresh ThreadPoolExecutor on every gather / extra-chain rebuild. Created
# lazily on first use (not at import) so tests and tooling that merely import
# this module don't pay for a thread pool.
_MARKET_IO_EXECUTOR = None


def _get_market_io_executor() -> ThreadPoolExecutor:
    global _MARKET_IO_EXECUTOR
    if _MARKET_IO_EXECUTOR is None:
        _MARKET_IO_EXECUTOR = ThreadPoolExecutor(max_workers=8)
        atexit.register(_MARKET_IO_EXECUTOR.shutdown)
    return _MARKET_IO_EXECUTOR


def _build_extra_chains(em, runtime_config, broker_adapters=None, timings=None):
    """NEAR and MONTHLY extra-expiry bundles, built concurrently (they're
    independent of each other) and throttled.

    A rebuilt bundle is cached per (symbol, slot, expiry date) and reused until
    it goes stale (TTL) or the symbol/expiry rolls — this avoids re-running the
    full option-chain fetch + analytics engine for the extra expiries on every
    poll, the single biggest source of redundant pipeline work. Empty dict when
    disabled or nothing pending."""
    extra_chains = {}
    if em is None or runtime_config.no_extra_chains:
        return extra_chains
    symbol = runtime_config.symbol
    now = time.monotonic()
    # Bound the cache: drop entries for any symbol other than the active one
    # (only one symbol is live per process at a time).
    if _extra_chain_cache:
        for _k in [k for k in _extra_chain_cache if k[0] != symbol]:
            del _extra_chain_cache[_k]
    try:
        slots = [
            (slot_name, slot)
            for slot_name, slot in [
                ("NEAR", em.context.near),
                ("MONTHLY", em.context.monthly),
            ]
            if slot and slot.date_str != str(runtime_config.expiry)
        ]
        if slots:
            to_build = []
            for slot_name, slot in slots:
                key = (symbol, slot_name, slot.date_str)
                cached = _extra_chain_cache.get(key)
                if (
                    cached is not None
                    and (now - cached[0]) < _EXTRA_CHAIN_CACHE_TTL_SECONDS
                ):
                    extra_chains[slot.date_str] = cached[1]
                    if timings is not None:
                        timings["extra" + slot_name] = 0.0
                else:
                    to_build.append((slot_name, slot, key))
            if to_build:
                futs = {}
                for slot_name, slot, key in to_build:
                    submitted_at = time.monotonic()
                    f = _get_market_io_executor().submit(
                        _build_expiry_bundle,
                        symbol,
                        slot.date_str,
                        _exchange_for_symbol(symbol),
                        runtime_config=runtime_config,
                        broker_adapters=broker_adapters,
                    )
                    futs[f] = (slot_name, slot, key, submitted_at)
                try:
                    completed = as_completed(
                        futs, timeout=_EXTRA_CHAIN_OPERATION_TIMEOUT_SECONDS
                    )
                    for f in completed:
                        slot_name, slot, key, submitted_at = futs[f]
                        try:
                            n_df, n_master, n_ctx, n_dte, _ = f.result()
                            value = (n_df, n_master, n_ctx, n_dte)
                            extra_chains[slot.date_str] = value
                            _extra_chain_cache[key] = (now, value)
                        except Exception as e:
                            logger.warning(f"[{slot_name}] Skip extra bundle ({e})")
                        if timings is not None:
                            timings["extra" + slot_name] = round(
                                time.monotonic() - submitted_at, 4
                            )
                except FutureTimeoutError:
                    for f, (slot_name, _slot, _key, submitted_at) in futs.items():
                        if not f.done():
                            f.cancel()
                            logger.warning(
                                "[%s] Skip extra bundle (operation timed out)",
                                slot_name,
                            )
                            if timings is not None:
                                timings["extra" + slot_name] = round(
                                    time.monotonic() - submitted_at, 4
                                )
    except Exception as e:
        logger.warning(f"[ExtraChains] Skip ({e})")
    return extra_chains


def _calendar_spread_expiries(em):
    """Calendar spread convention: sell the active expiry (front week/month
    already being traded), buy the next MONTHLY expiry — both real, verified,
    future-filtered dates from ExpiryManager. Falls back to "" (→ engine's
    "NEAR"/"FAR" text placeholders) only if em wasn't available at all."""
    if em is None:
        return "", ""
    far = (
        em.context.monthly.date_str
        if em.context.monthly
        else em.context.far.date_str
        if em.context.far
        else ""
    )
    return em.context.current.date_str, far


def _patch_bse_spot_change(ctx_dict, all_indices, runtime_config):
    """SENSEX never appears in df_idx (fetch_all_indices()/DEFAULT_INDICES
    is NSE-only), so engine.py's spot_change/spot_chg_pct lookup falls back
    to 0 when SENSEX is the ACTIVE symbol. Patch from the BSE quote already
    fetched in the fan-out instead of touching engine.py's NSE lookup."""
    if runtime_config.symbol not in _BSE_SYMBOLS:
        return
    quote = next(
        (
            q
            for q in all_indices
            if q.get("Symbol") == runtime_config.symbol
        ),
        None,
    )
    if not quote:
        return
    if quote.get("Change") is not None:
        ctx_dict["spot_change"] = quote["Change"]
    if quote.get("% Change") is not None:
        ctx_dict["spot_chg_pct"] = quote["% Change"]


# =====================================================================
# PIPELINE EXECUTION
# =====================================================================


def main(
    runtime_config: RuntimeConfig | None = None,
    broker_adapters: BrokerMarketAdapters | None = None,
    export_dashboard: Callable | None = None,
):
    """Run one analytics pass using an explicit runtime configuration.

    All helpers consume this pass-scoped value; no module selection state is
    mutated between runs.
    """
    runtime_config = runtime_config or _DEFAULT_RUNTIME_CONFIG
    if export_dashboard is None:
        raise RuntimeError("dashboard exporter must be injected")
    exchange = _exchange_for_symbol(runtime_config.symbol)
    t_start = time.monotonic()
    timings: dict = {"_pipelineStartedAt": t_start}

    try:
        md = _gather_market_data(
            exchange, runtime_config, broker_adapters, timings=timings
        )
        df, spot = md["df"], md["spot"]

        _quote_keys = ("ticker", "vix", "sensex") + tuple(
            k for k in timings if k.startswith("publicBse:")
        )
        timings["quotes"] = round(
            max([timings.get(k, 0.0) for k in _quote_keys] or [0.0]), 4
        )

        resolved_expiry = (
            runtime_config.expiry
            if exchange == "BSE"
            else md["resolved"]
        )

        if spot == 0 or spot is None:
            logger.error("Error: Invalid Spot Price. Core calculations aborted.")
            return

        # NOTE (2026-07-04): the per-tick joblib.load() of a VirtualOI
        # coordinator that happened here every poll was dead code — its
        # result was never passed anywhere. The real coordinator lives in
        # mTerminals_json.py as a module-level _VOI_COORDINATOR, loaded
        # once per process; --no-virtual-oi flows through export_dashboard_json().

        df_idx = md["df_idx"]
        all_indices = md["all_indices"]

        # Derived from df_idx (already fetched, no new network call) —
        # empty list for symbols with no matching NSE index basket (BSE).
        contributors = _compute_index_contributors(
            df_idx, runtime_config.symbol, spot
        )

        dte = compute_dte(resolved_expiry)
        df_clean = (
            df.dropna(subset=["StrikePrice"])
            .drop_duplicates(subset=["StrikePrice"])
            .sort_values("StrikePrice")
            .copy()
        )

        em = _make_expiry_manager_or_none(md["expiry_dates"])
        extra_chains = _build_extra_chains(
            em, runtime_config, broker_adapters, timings=timings
        )

        # Fallback to local JSON snap logs for historical OI analysis.
        prev_json_poll = read_last_json_snapshot(runtime_config.symbol)
        history_df = build_oi_history(
            df_clean, runtime_config.symbol, prev_poll=prev_json_poll
        )
        append_json_history(history_df)

        _near_expiry_str, _far_expiry_str = _calendar_spread_expiries(em)

        _engine_start = time.monotonic()
        engine_result = build_engine_result(
            df=df,
            df_clean=df_clean,
            df_idx=df_idx,
            df_fut=md["df_fut"],
            df_full_history=history_df,
            symbol=runtime_config.symbol,
            expiry=resolved_expiry,
            dte=dte,
            lot_size=LOT_SIZES.get(runtime_config.symbol, 65),
            n_strikes_each_side=runtime_config.strikes_each_side,
            india_vix=md["india_vix"],
            india_vix_chg_pct=md["india_vix_chg_pct"],
            near_expiry=_near_expiry_str,
            far_expiry=_far_expiry_str,
        )
        timings["engine"] = round(time.monotonic() - _engine_start, 4)

        ctx_dict = engine_result.to_ctx_dict()
        _patch_bse_spot_change(ctx_dict, all_indices, runtime_config)

        export_dashboard(
            df_clean=df_clean,
            master=engine_result.master,
            ctx_dict=ctx_dict,
            SYMBOL=runtime_config.symbol,
            EXPIRY=resolved_expiry,
            dte=dte,
            engine_result=engine_result,
            out_path="mTerminals.json",
            expiry_dates=md["expiry_dates"],
            extra_chains=extra_chains if extra_chains else None,
            use_virtual_oi=not runtime_config.no_virtual_oi,
            contributors=contributors,
            all_indices=all_indices,
            price_source=md["price_source_used"],
            futures_expiry=runtime_config.futures_expiry,
            pipeline_timings=timings,
        )
        print()
        logger.info("SUCCESS: JSON Framework updated snapshot successfully.")

    except Exception as exc:
        logger.error(
            "analytics tick failed for %s %s: %s",
            runtime_config.symbol,
            runtime_config.expiry,
            exc,
        )


# =====================================================================
# STANDALONE CLI — argv is parsed HERE and nowhere else
# =====================================================================


def _build_arg_parser(default_expiry=None):
    parser = argparse.ArgumentParser(prog="option_chain_json", add_help=True)
    parser.add_argument("--exchange", default="NSE", choices=["NSE", "BSE"])
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--interval", default=0, type=int)
    parser.add_argument(
        "--no-extra-chains",
        action="store_true",
        help="Disable multi-expiry chains for faster performance",
    )
    parser.add_argument(
        "--strict-expiry",
        action="store_true",
        help="Don't auto-resolve to different expiry if requested expiry has no data",
    )
    parser.add_argument(
        "--no-virtual-oi",
        action="store_true",
        help="Disable VirtualOI model inference for faster performance",
    )
    if default_expiry is not None:
        # Second parse stage: --expiry's default depends on --symbol's
        # value (BSE indices expire Thursdays, NSE Tuesdays), mirroring
        # the original two-pass parse_known_args() behavior exactly.
        parser.add_argument("--expiry", default=default_expiry, help="Expiry DD-Mmm-YYYY")
    return parser


def _log_init_banner(runtime_config, exchange, loop_interval):
    logger.info("=== LIGHTWEIGHT JSON OPTIONS PIPELINE INITIALIZATION ===")
    logger.info(
        f"    Exchange: {exchange} | Symbol: {runtime_config.symbol} | "
        f"Expiry: {runtime_config.expiry}"
    )
    logger.info(
        f"    Loop    : "
        f"{'every ' + str(loop_interval) + ' min' if loop_interval > 0 else 'single run'}\n"
    )


def _apply_cli_overrides(argv=None):
    """Parse standalone CLI inputs into an explicit runtime configuration."""
    pre, _ = _build_arg_parser().parse_known_args(argv)
    sym = (pre.symbol or "NIFTY").strip().upper()
    default_expiry = (
        BSE_EXPIRY_DEFAULT.get(sym, _nearest_Thursday)()
        if sym in _BSE_SYMBOLS
        else _nearest_Tuesday()
    )
    args, _unknown = _build_arg_parser(default_expiry).parse_known_args(argv)

    runtime_config = RuntimeConfig(
        symbol=args.symbol.strip().upper(),
        expiry=args.expiry.strip(),
        no_extra_chains=args.no_extra_chains,
        strict_expiry=args.strict_expiry,
        no_virtual_oi=args.no_virtual_oi,
        strikes_each_side=_DEFAULT_RUNTIME_CONFIG.strikes_each_side,
        use_smartapi=_DEFAULT_RUNTIME_CONFIG.use_smartapi,
        price_source=_DEFAULT_RUNTIME_CONFIG.price_source,
        futures_expiry=_DEFAULT_RUNTIME_CONFIG.futures_expiry,
    )
    exchange = args.exchange.strip().upper()
    _log_init_banner(runtime_config, exchange, args.interval)
    return runtime_config, args.interval


if __name__ == "__main__":
    _cli_runtime_config, _loop_interval = _apply_cli_overrides()
    if _loop_interval > 0:
        logger.info(
            f"[Loop] Active monitoring interval: {_loop_interval} min. "
            "Use Ctrl+C to terminate.\n"
        )
        while True:
            raise RuntimeError(
                "standalone analytics execution requires a composed exporter"
            )
            time.sleep(_loop_interval * 60)
    else:
        raise RuntimeError(
            "standalone analytics execution requires a composed exporter"
        )
