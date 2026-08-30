"""Assembly boundary for the server's option-chain analytics runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from analytics.option_chain_pipeline import OptionChainPipeline
from application.analytics_service import (
    AnalyticsPipelineRunner,
    PipelineRuntimeConfigurator,
)
from application.market_service import SerializedPipelineExecutor
from brokers.expiry_adapter import BrokerExpiryAdapter
from brokers.option_chain_adapter import BrokerOptionChainAdapter
from market.option_chain.runtime_adapters import BrokerMarketAdapters


def build_broker_market_adapters() -> BrokerMarketAdapters:
    """Bind broker-backed analytics functions to their domain interfaces."""
    from application.market_pipeline.futures import fetch_futures_wide
    from application.market_pipeline.option_chain import (
        fetch_option_chain_wide,
        get_available_expiries,
    )
    from application.market_pipeline.quotes import (
        fetch_all_pills_and_vix_batched,
        fetch_sensex_ticker,
        fetch_ticker_payload,
        fetch_vix,
    )
    from application.market_pipeline.utils import _canon_underlying

    chain = BrokerOptionChainAdapter(
        fetch_chain=fetch_option_chain_wide,
        canonicalize_symbol=_canon_underlying,
    )
    expiries = BrokerExpiryAdapter(fallback=get_available_expiries)
    return BrokerMarketAdapters(
        canonicalize_symbol=chain.canonicalize,
        fetch_chain=chain.fetch,
        list_expiries=expiries.list_expiries,
        fetch_futures=lambda symbol, exchange, which: fetch_futures_wide(
            symbol, None, exchange=exchange, which=which
        ),
        warm_batch=fetch_all_pills_and_vix_batched,
        fetch_ticker_payload=fetch_ticker_payload,
        fetch_vix=fetch_vix,
        fetch_sensex_quote=fetch_sensex_ticker,
    )


class AnalyticsRuntime:
    """Own configuration, capture, execution, and serialization for analytics."""

    def __init__(
        self,
        *,
        symbol: Callable[[], str],
        expiry: Callable[[], str],
        data_source: Callable[[], str],
        price_source: Callable[[], str],
        futures_expiry: Callable[[], str],
        strikes_each_side: Callable[[], int],
        activate_provider: Callable[[str], Any],
        resolve_default_expiry: Callable[[str], str],
        apply_config: Callable[[Any], Any],
        clear_capture: Callable[[], Any],
        captured_payload: Callable[[], Any],
        export_dashboard: Callable[..., Any],
        invoke_analytics: Callable[..., Any],
        broker_adapters: BrokerMarketAdapters | None,
        extra_chains: bool,
        strict_expiry: bool,
        no_virtual_oi: bool,
        operation_timeout_seconds: float,
    ) -> None:
        self._symbol = symbol
        self._expiry = expiry
        self._price_source = price_source
        self._futures_expiry = futures_expiry
        self._strikes_each_side = strikes_each_side
        self._extra_chains = extra_chains
        self._strict_expiry = strict_expiry
        self._no_virtual_oi = no_virtual_oi
        self._operation_timeout_seconds = operation_timeout_seconds
        self._configurator = PipelineRuntimeConfigurator(
            data_source=data_source,
            activate_provider=activate_provider,
            resolve_default_expiry=resolve_default_expiry,
            apply_config=apply_config,
        )
        pipeline = OptionChainPipeline(
            implementation=lambda config: invoke_analytics(
                config,
                broker_adapters=broker_adapters,
                export_dashboard=export_dashboard,
            )
        )
        self._runner = AnalyticsPipelineRunner(
            configure=self.configure_current,
            clear_capture=clear_capture,
            invoke=pipeline.run,
            captured_payload=captured_payload,
        )
        self._executor = SerializedPipelineExecutor()

    def configure(self, symbol: str, expiry: str | None = None):
        """Build a runtime config, primarily for diagnostics and tests."""
        return self._configurator.configure(
            symbol=symbol,
            expiry=expiry,
            strikes_each_side=self._strikes_each_side(),
            operation_timeout_seconds=self._operation_timeout_seconds,
        )

    def configure_current(self):
        return self._configurator.configure(
            symbol=self._symbol(),
            expiry=self._expiry(),
            no_extra_chains=not self._extra_chains,
            strict_expiry=self._strict_expiry,
            no_virtual_oi=self._no_virtual_oi,
            price_source=self._price_source(),
            futures_expiry=self._futures_expiry(),
            strikes_each_side=self._strikes_each_side(),
            operation_timeout_seconds=self._operation_timeout_seconds,
        )

    async def run(self):
        return await self._executor.run_blocking(self._runner.run_once)

    @property
    def execution_gate(self) -> SerializedPipelineExecutor:
        return self._executor
