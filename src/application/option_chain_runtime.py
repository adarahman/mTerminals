"""Lightweight JSON options pipeline (refactored).

Refactor of the former import-time-heavy module:

- Hosts pass one RuntimeConfig directly to main(); executable process setup
  belongs to the canonical ``main`` module and ``server.app`` composition root.
- main() delegates market input gathering, normalization, and secondary-expiry
  bundle construction to focused application services, each independently
  readable/testable; control flow and ordering inside each stage match the
  previous monolith exactly.
- Expiry generation is delegated to ``market.expiry.service``.

Pipeline helpers consume the explicit pass-scoped RuntimeConfig.
"""

import logging
import time
from collections.abc import Callable
from decision.engine import build_engine_result
from market.expiry.service import (
    _generate_bse_expiry_series,
    _nearest_Tuesday,
)
from market.instruments.lot_sizes import LOT_SIZES

from market.providers.option_chain import PublicOptionChainAdapter
from oi.oi_analysis import compute_dte
from application.pipeline_config import RuntimeConfig
from application.market_pipeline.spot_selection import select_runtime_spot
from application.market_pipeline.context import assemble_market_context
from application.market_pipeline.index_cache import IndexSnapshotCache
from application.market_pipeline.resources import (
    ChainSnapshotStore,
    RetirableExecutorPool,
)
from application.market_pipeline.extra_chains import ExtraChainService
from application.market_pipeline.snapshot import AnalyticsSnapshotService
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


# ─── Default runtime configuration ──────────────────────────────────
# Used only when an embedding caller omits an explicit RuntimeConfig.
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
_SNAPSHOT_SERVICE = AnalyticsSnapshotService(
    extra_chains=_EXTRA_CHAINS,
    logger=logger,
)


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
        _SNAPSHOT_SERVICE.build_and_export(
            market_data=md,
            runtime_config=runtime_config,
            exchange=exchange,
            broker_adapters=broker_adapters,
            timings=timings,
            export_dashboard=export_dashboard,
        )

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
