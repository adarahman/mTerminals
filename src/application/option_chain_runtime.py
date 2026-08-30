"""Lightweight JSON options pipeline (refactored).

Behavior-preserving refactor of the former import-time-heavy module:

- CLI arguments are parsed ONLY when run as a script (__main__), never at
  import. Hosts pass one RuntimeConfig directly to main().
- main() delegates market input gathering, normalization, and secondary-expiry
  bundle construction to focused application services, each independently
  readable/testable; control flow and ordering inside each stage match the
  previous monolith exactly.
- Expiry generation is delegated to ``market.expiry.service``.

Pipeline helpers consume the explicit pass-scoped RuntimeConfig.
"""

import argparse
import logging
import time
from collections.abc import Callable
from decision.engine import build_engine_result
from market.expiry.service import (
    BSE_EXPIRY_DEFAULT,
    _generate_bse_expiry_series,
    _nearest_Thursday,
    _nearest_Tuesday,
    make_expiry_manager,
)
from analytics.index_contributors import _compute_index_contributors
from market.instruments.lot_sizes import LOT_SIZES

from market.providers.option_chain import PublicOptionChainAdapter
from oi.oi_analysis import (
    append_json_history,
    build_oi_history,
    compute_dte,
    read_last_json_snapshot,
)
from application.pipeline_config import RuntimeConfig
from application.market_pipeline.spot_selection import select_runtime_spot
from application.market_pipeline.context import assemble_market_context
from application.market_pipeline.index_cache import IndexSnapshotCache
from application.market_pipeline.resources import (
    ChainSnapshotStore,
    RetirableExecutorPool,
)
from application.market_pipeline.extra_chains import ExtraChainService
from market.option_chain.requests import MarketDataRequestPlan
from market.option_chain.gatherer import ConcurrentMarketDataGatherer
from market.option_chain.runtime_adapters import BrokerMarketAdapters
from market.option_chain.service import (
    ExpiryResolutionService,
    OptionChainFetchService,
)

logger = logging.getLogger(__name__)

# Expiry-date generation, lot-size resolution, and index contributors live in
# their respective market/analytics services.

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
    operation_timeout_seconds=15.0,
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
_INDEX_SNAPSHOTS = IndexSnapshotCache(
    fetch=_PUBLIC_MARKET.fetch_indices,
    ttl_seconds=DF_IDX_TTL_SECONDS,
    logger=logger,
)


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


_select_runtime_spot = select_runtime_spot


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

    from brokers.market_data_registry import get_active_provider

    source = get_active_provider() if request.broker_enabled else "NSE_BSE"
    chain_cache_key = (
        source,
        request.symbol,
        request.option_expiry,
        request.option_exchange,
        request.strict_expiry,
    )

    def fetch_chain(plan):
        value = _fetch_and_parse(
            plan.symbol,
            plan.option_expiry,
            plan.option_exchange,
            plan.strict_expiry,
            runtime_config,
            broker_adapters,
        )
        _CHAIN_SNAPSHOTS.remember(chain_cache_key, value)
        return value

    def fallback_chain(_plan):
        return _CHAIN_SNAPSHOTS.load(
            chain_cache_key,
            source=source,
            timings=timings,
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

    try:
        gathered = ConcurrentMarketDataGatherer(
            fetch_chain=fetch_chain,
            fetch_futures=fetch_futures,
            fetch_indices=_INDEX_SNAPSHOTS.get,
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
            fallback_chain=fallback_chain,
            executor=_MARKET_IO_POOL.get(),
            operation_timeout_seconds=(
                runtime_config.operation_timeout_seconds or 15.0
            ),
        ).gather(request, timings=timings)
    except TimeoutError:
        # Running Python threads cannot be forcibly stopped. Retire this pool
        # so a broker SDK call that ignores its deadline cannot consume worker
        # capacity from every later analytics tick.
        _MARKET_IO_POOL.retire()
        raise

    if "chain" in gathered.stale_operations:
        # The timed-out thread may still be blocked inside a broker SDK call.
        # Retire its pool so later ticks start with fresh worker capacity.
        _MARKET_IO_POOL.retire()

    return assemble_market_context(
        gathered=gathered,
        request=request,
        runtime_config=runtime_config,
        unified_public_market_data=_PUBLIC_MARKET.unified_market_data,
        select_spot=_select_runtime_spot,
    )


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


_CHAIN_FALLBACK_MAX_AGE_SECONDS = 300.0
_CHAIN_SNAPSHOTS = ChainSnapshotStore(
    max_age_seconds=_CHAIN_FALLBACK_MAX_AGE_SECONDS,
    logger=logger,
)
_MARKET_IO_POOL = RetirableExecutorPool(max_workers=8)
_EXTRA_CHAINS = ExtraChainService(
    build_bundle=_build_expiry_bundle,
    exchange_for_symbol=_exchange_for_symbol,
    executor_pool=_MARKET_IO_POOL,
    logger=logger,
)


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
        extra_chains = _EXTRA_CHAINS.build(
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

    except TimeoutError:
        # The server runner distinguishes a recoverable cold-start miss from
        # consecutive timeouts. Preserve that signal across this boundary.
        raise
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
