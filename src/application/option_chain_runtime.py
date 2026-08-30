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
from market.providers.option_chain import PublicOptionChainAdapter
from application.pipeline_config import RuntimeConfig
from application.market_pipeline.index_cache import IndexSnapshotCache
from application.market_pipeline.resources import (
    ChainSnapshotStore,
    RetirableExecutorPool,
)
from application.market_pipeline.extra_chains import ExtraChainService
from application.market_pipeline.snapshot import AnalyticsSnapshotService
from application.market_pipeline.chain_service import ChainAnalyticsService
from application.market_pipeline.input_service import MarketInputService
from market.option_chain.runtime_adapters import BrokerMarketAdapters

logger = logging.getLogger(__name__)

# Expiry-date generation, lot-size resolution, and index contributors live in
# their respective market/analytics services.

_BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}
_PUBLIC_MARKET = PublicOptionChainAdapter()
_CHAIN_SERVICE = ChainAnalyticsService(public_market=_PUBLIC_MARKET)


def _exchange_for_symbol(symbol: str) -> str:
    return "BSE" if symbol in _BSE_SYMBOLS else "NSE"


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


_CHAIN_FALLBACK_MAX_AGE_SECONDS = 300.0
_CHAIN_SNAPSHOTS = ChainSnapshotStore(
    max_age_seconds=_CHAIN_FALLBACK_MAX_AGE_SECONDS,
    logger=logger,
)
_MARKET_IO_POOL = RetirableExecutorPool(max_workers=8)


def _active_market_provider():
    from brokers.market_data_registry import get_active_provider

    return get_active_provider()


_MARKET_INPUTS = MarketInputService(
    chain_service=_CHAIN_SERVICE,
    chain_snapshots=_CHAIN_SNAPSHOTS,
    executor_pool=_MARKET_IO_POOL,
    index_snapshots=_INDEX_SNAPSHOTS,
    public_market=_PUBLIC_MARKET,
    active_provider=_active_market_provider,
)
_EXTRA_CHAINS = ExtraChainService(
    build_bundle=_CHAIN_SERVICE.build_expiry_bundle,
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
    runtime_config: RuntimeConfig,
    broker_adapters: BrokerMarketAdapters | None = None,
    export_dashboard: Callable | None = None,
):
    """Run one analytics pass using an explicit runtime configuration.

    All helpers consume this pass-scoped value; no module selection state is
    mutated between runs.
    """
    if export_dashboard is None:
        raise RuntimeError("dashboard exporter must be injected")
    exchange = _exchange_for_symbol(runtime_config.symbol)
    t_start = time.monotonic()
    timings: dict = {"_pipelineStartedAt": t_start}

    try:
        md = _MARKET_INPUTS.gather(
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
