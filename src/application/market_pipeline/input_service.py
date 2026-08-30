"""Gather and normalize all market inputs required by one analytics pass."""

from __future__ import annotations

from application.market_pipeline.context import assemble_market_context
from application.market_pipeline.resources import (
    ChainSnapshotStore,
    RetirableExecutorPool,
)
from application.market_pipeline.spot_selection import select_runtime_spot
from application.pipeline_config import RuntimeConfig
from market.option_chain.gatherer import ConcurrentMarketDataGatherer
from market.option_chain.requests import MarketDataRequestPlan
from market.option_chain.runtime_adapters import BrokerMarketAdapters


class MarketInputService:
    """Fan out provider reads and assemble one normalized market context."""

    def __init__(
        self,
        *,
        chain_service,
        chain_snapshots: ChainSnapshotStore,
        executor_pool: RetirableExecutorPool,
        index_snapshots,
        public_market,
        active_provider,
        gatherer_factory=ConcurrentMarketDataGatherer,
        spot_selector=select_runtime_spot,
    ) -> None:
        self._chain_service = chain_service
        self._chain_snapshots = chain_snapshots
        self._executor_pool = executor_pool
        self._index_snapshots = index_snapshots
        self._public_market = public_market
        self._active_provider = active_provider
        self._gatherer_factory = gatherer_factory
        self._spot_selector = spot_selector

    def gather(
        self,
        exchange: str,
        runtime_config: RuntimeConfig,
        broker_adapters: BrokerMarketAdapters | None = None,
        timings: dict | None = None,
    ) -> dict:
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
        source = self._active_provider() if request.broker_enabled else "NSE_BSE"
        chain_key = (
            source,
            request.symbol,
            request.option_expiry,
            request.option_exchange,
            request.strict_expiry,
        )

        def fetch_chain(plan):
            value = self._chain_service.fetch(
                plan.symbol,
                plan.option_expiry,
                plan.option_exchange,
                strict_expiry=plan.strict_expiry,
                runtime_config=runtime_config,
                broker_adapters=broker_adapters,
            )
            self._chain_snapshots.remember(chain_key, value)
            return value

        def fallback_chain(_plan):
            return self._chain_snapshots.load(
                chain_key,
                source=source,
                timings=timings,
            )

        def fetch_futures(plan):
            if not plan.broker_enabled:
                return self._public_market.fetch_futures(
                    plan.symbol, plan.futures_expiry
                )
            return broker_adapters.fetch_futures(
                plan.symbol,
                plan.broker_derivatives_exchange,
                plan.futures_expiry,
            )

        try:
            gathered = self._gatherer_factory(
                fetch_chain=fetch_chain,
                fetch_futures=fetch_futures,
                fetch_indices=self._index_snapshots.get,
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
                fetch_public_bse_quote=self._public_market.fetch_bse_quote,
                public_bse_symbols=self._public_market.bse_symbols,
                fallback_chain=fallback_chain,
                executor=self._executor_pool.get(),
                operation_timeout_seconds=(
                    runtime_config.operation_timeout_seconds or 15.0
                ),
            ).gather(request, timings=timings)
        except TimeoutError:
            self._executor_pool.retire()
            raise

        if "chain" in gathered.stale_operations:
            self._executor_pool.retire()

        return assemble_market_context(
            gathered=gathered,
            request=request,
            runtime_config=runtime_config,
            unified_public_market_data=self._public_market.unified_market_data,
            select_spot=self._spot_selector,
        )
