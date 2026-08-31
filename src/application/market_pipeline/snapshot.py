"""Build and export one analytics snapshot from normalized market inputs."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from analytics.index_contributors import _compute_index_contributors
from application.market_pipeline.extra_chains import ExtraChainService
from application.pipeline_config import RuntimeConfig
from decision.engine import build_engine_result
from market.expiry.service import make_expiry_manager
from market.instruments.lot_sizes import LOT_SIZES
from oi.oi_analysis import (
    append_json_history,
    build_oi_history,
    compute_dte,
    read_last_json_snapshot,
)


class AnalyticsSnapshotService:
    """Own analytics calculation, history update, and dashboard export."""

    def __init__(
        self,
        *,
        extra_chains: ExtraChainService,
        logger,
        engine_builder: Callable = build_engine_result,
        contributors_builder: Callable = _compute_index_contributors,
        expiry_manager_factory: Callable = make_expiry_manager,
        dte_calculator: Callable = compute_dte,
        history_reader: Callable = read_last_json_snapshot,
        history_builder: Callable = build_oi_history,
        history_appender: Callable = append_json_history,
        lot_sizes: Mapping[str, int] = LOT_SIZES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._extra_chains = extra_chains
        self._logger = logger
        self._engine_builder = engine_builder
        self._contributors_builder = contributors_builder
        self._expiry_manager_factory = expiry_manager_factory
        self._dte_calculator = dte_calculator
        self._history_reader = history_reader
        self._history_builder = history_builder
        self._history_appender = history_appender
        self._lot_sizes = lot_sizes
        self._clock = clock

    def build_and_export(
        self,
        *,
        market_data: dict,
        runtime_config: RuntimeConfig,
        exchange: str,
        broker_adapters,
        timings: dict,
        export_dashboard: Callable,
    ) -> bool:
        frame, spot = market_data["df"], market_data["spot"]
        quote_keys = ("ticker", "vix", "sensex") + tuple(
            key for key in timings if key.startswith("publicBse:")
        )
        timings["quotes"] = round(
            max([timings.get(key, 0.0) for key in quote_keys] or [0.0]), 4
        )
        resolved_expiry = (
            runtime_config.expiry
            if exchange == "BSE"
            else market_data["resolved"]
        )
        if spot == 0 or spot is None:
            self._logger.error(
                "Error: Invalid Spot Price. Core calculations aborted."
            )
            return False

        indices = market_data["df_idx"]
        all_indices = market_data["all_indices"]
        contributors = self._contributors_builder(
            indices, runtime_config.symbol, spot
        )
        dte = self._dte_calculator(resolved_expiry)
        clean_frame = (
            frame.dropna(subset=["StrikePrice"])
            .drop_duplicates(subset=["StrikePrice"])
            .sort_values("StrikePrice")
            .copy()
        )
        from brokers.market_data_registry import get_active_provider
        data_provider = get_active_provider()
        clean_frame.attrs["data_provider"] = data_provider
        expiry_manager = self._expiry_manager(market_data["expiry_dates"])
        extra_chains = self._extra_chains.build(
            expiry_manager,
            runtime_config,
            broker_adapters,
            timings=timings,
        )
        previous = self._history_reader(runtime_config.symbol)
        history = self._history_builder(
            clean_frame, runtime_config.symbol, prev_poll=previous
        )
        if hasattr(history, "columns"):
            history["Provider"] = data_provider
        self._history_appender(history)
        near_expiry, far_expiry = self._calendar_spread_expiries(expiry_manager)

        engine_started = self._clock()
        engine_result = self._engine_builder(
            df=frame,
            df_clean=clean_frame,
            df_idx=indices,
            df_fut=market_data["df_fut"],
            df_full_history=history,
            symbol=runtime_config.symbol,
            expiry=resolved_expiry,
            dte=dte,
            lot_size=self._lot_sizes.get(runtime_config.symbol, 65),
            n_strikes_each_side=runtime_config.strikes_each_side,
            india_vix=market_data["india_vix"],
            india_vix_chg_pct=market_data["india_vix_chg_pct"],
            near_expiry=near_expiry,
            far_expiry=far_expiry,
        )
        timings["engine"] = round(self._clock() - engine_started, 4)
        context = engine_result.to_ctx_dict()
        self._patch_bse_spot_change(
            context, all_indices, runtime_config.symbol
        )
        export_dashboard(
            df_clean=clean_frame,
            master=engine_result.master,
            ctx_dict=context,
            SYMBOL=runtime_config.symbol,
            EXPIRY=resolved_expiry,
            dte=dte,
            engine_result=engine_result,
            out_path="mTerminals.json",
            expiry_dates=market_data["expiry_dates"],
            extra_chains=extra_chains or None,
            use_virtual_oi=not runtime_config.no_virtual_oi,
            contributors=contributors,
            all_indices=all_indices,
            price_source=market_data["price_source_used"],
            futures_expiry=runtime_config.futures_expiry,
            pipeline_timings=timings,
        )
        self._logger.info("SUCCESS: JSON Framework updated snapshot successfully.")
        return True

    def _expiry_manager(self, expiry_dates):
        if not expiry_dates:
            return None
        try:
            return self._expiry_manager_factory(expiry_dates)
        except Exception as exc:
            self._logger.warning("[ExpiryManager] Context skip (%s)", exc)
            return None

    @staticmethod
    def _calendar_spread_expiries(expiry_manager):
        if expiry_manager is None:
            return "", ""
        context = expiry_manager.context
        far = (
            context.monthly.date_str
            if context.monthly
            else context.far.date_str
            if context.far
            else ""
        )
        return context.current.date_str, far

    @staticmethod
    def _patch_bse_spot_change(context, all_indices, symbol):
        if symbol not in {"SENSEX", "BANKEX", "SENSEX50"}:
            return
        quote = next(
            (item for item in all_indices if item.get("Symbol") == symbol),
            None,
        )
        if not quote:
            return
        if quote.get("Change") is not None:
            context["spot_change"] = quote["Change"]
        if quote.get("% Change") is not None:
            context["spot_chg_pct"] = quote["% Change"]
