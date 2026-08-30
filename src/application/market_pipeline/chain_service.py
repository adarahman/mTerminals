"""Option-chain fetching and secondary-expiry analytics construction."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from application.pipeline_config import RuntimeConfig
from decision.engine import build_engine_result
from market.expiry.service import _generate_bse_expiry_series
from market.instruments.lot_sizes import LOT_SIZES
from market.option_chain.requests import MarketDataRequestPlan
from market.option_chain.runtime_adapters import BrokerMarketAdapters
from market.option_chain.service import ExpiryResolutionService, OptionChainFetchService
from oi.oi_analysis import compute_dte


class ChainAnalyticsService:
    """Fetch canonical option chains and build expiry-specific analytics."""

    def __init__(
        self,
        *,
        public_market,
        engine_builder: Callable = build_engine_result,
        dte_calculator: Callable = compute_dte,
        lot_sizes: Mapping[str, int] = LOT_SIZES,
        expiry_resolver: ExpiryResolutionService | None = None,
    ) -> None:
        self._public_market = public_market
        self._engine_builder = engine_builder
        self._dte_calculator = dte_calculator
        self._lot_sizes = lot_sizes
        self._expiry_resolver = expiry_resolver or ExpiryResolutionService()

    @staticmethod
    def canonicalize(
        symbol: str,
        runtime_config: RuntimeConfig,
        broker_adapters: BrokerMarketAdapters | None = None,
    ) -> str:
        raw = (symbol or "").strip().upper()
        if not runtime_config.broker_enabled or not raw:
            return raw
        if broker_adapters is None:
            raise RuntimeError("broker adapters are required in broker mode")
        return broker_adapters.canonicalize_symbol(raw)

    def fetch(
        self,
        symbol: str,
        expiry: str,
        exchange: str,
        *,
        strict_expiry: bool,
        runtime_config: RuntimeConfig,
        broker_adapters: BrokerMarketAdapters | None = None,
    ):
        symbol = self.canonicalize(symbol, runtime_config, broker_adapters)
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
            fetch_public_bse_chain=self._public_market.fetch_bse_chain,
            fetch_public_nse_payload=self._public_market.fetch_nse_payload,
            parse_public_nse_payload=self._public_market.parse_nse_payload,
            fetch_bse_quote=self._public_market.fetch_bse_quote,
            generate_bse_expiries=_generate_bse_expiry_series,
            expiry_resolver=self._expiry_resolver,
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

    def build_expiry_bundle(
        self,
        symbol: str,
        expiry: str,
        exchange: str = "NSE",
        *,
        strict_expiry: bool = False,
        runtime_config: RuntimeConfig,
        broker_adapters: BrokerMarketAdapters | None = None,
        **engine_kwargs,
    ):
        symbol = self.canonicalize(symbol, runtime_config, broker_adapters)
        fetched = self.fetch(
            symbol,
            expiry,
            exchange,
            strict_expiry=strict_expiry,
            runtime_config=runtime_config,
            broker_adapters=broker_adapters,
        )
        if exchange == "BSE":
            frame, spot, _expiry_dates = fetched
            resolved = expiry
        else:
            frame, spot, resolved, _expiry_dates = fetched
        clean_frame = (
            frame.dropna(subset=["StrikePrice"])
            .drop_duplicates(subset=["StrikePrice"])
            .sort_values("StrikePrice")
            .copy()
        )
        dte = self._dte_calculator(resolved)
        engine_kwargs.pop("velocity_window_minutes", None)
        engine_result = self._engine_builder(
            df=frame,
            df_clean=clean_frame,
            df_idx=None,
            df_fut=None,
            df_full_history=None,
            symbol=symbol,
            expiry=resolved,
            dte=dte,
            lot_size=engine_kwargs.pop(
                "lot_size", self._lot_sizes.get(symbol, 65)
            ),
            n_strikes_each_side=engine_kwargs.pop(
                "n_strikes_each_side", runtime_config.strikes_each_side
            ),
            **engine_kwargs,
        )
        return (
            clean_frame,
            engine_result.master,
            engine_result.to_ctx_dict(),
            dte,
            resolved,
        )
