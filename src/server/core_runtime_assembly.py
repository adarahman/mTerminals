"""Assembly boundary for dashboard, analytics, selection, and feed services."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from application.market_service import DataSourceSwitcher, SymbolSwitcher
from application.dashboard import serializer as dashboard_serializer
from infrastructure.payload_capture import PayloadExportCapture
from server.analytics_runtime import AnalyticsRuntime, build_broker_market_adapters
from server.bridge import DashboardBridge
from server.dashboard_transport import DashboardBroadcaster
from server.feeds.orchestration import build_feed_managers
from server.paper_portfolio import PaperPortfolioService


def _build_payload_capture() -> PayloadExportCapture:
    def load_exported_payload():
        with open("mTerminals.json") as exported:
            return json.load(exported)

    return PayloadExportCapture(
        exporter=dashboard_serializer.export_dashboard_json,
        fallback_loader=load_exported_payload,
        export_overrides={"out_path": "mTerminals.json"},
    )


@dataclass(frozen=True, slots=True)
class CoreRuntime:
    broadcaster: DashboardBroadcaster
    broadcast: Callable[..., Any]
    paper_portfolio: PaperPortfolioService
    bridge: DashboardBridge
    analytics: AnalyticsRuntime
    symbol_switcher: SymbolSwitcher
    data_source_switcher: DataSourceSwitcher
    payload_capture: Any


def build_core_runtime(
    *,
    runtime_state: Any,
    args: Any,
    paper_engine: Any,
    paper_price_book: Any,
    instrument_key: Callable[..., str],
    origin_allowed: Callable[..., bool],
    json_default: Callable[..., Any],
    encode: Callable[[Any], str],
    market_api: Any,
    broker_futures_fetcher: Callable[..., Any],
    activate_provider: Callable[[str], Any],
    resolve_default_expiry: Callable[[str], str],
    invoke_analytics: Callable[..., Any],
    broker_services_enabled: bool,
    provider_keys: Any,
    supports_websocket: Callable[[str], bool],
    feed_manager: Any,
    report: Callable[..., Any],
) -> CoreRuntime:
    capture = _build_payload_capture()
    broadcaster = DashboardBroadcaster(
        runtime_state=runtime_state,
        encode=encode,
        report=report,
    )
    broadcast = broadcaster.broadcast
    paper_portfolio = PaperPortfolioService(
        engine=paper_engine,
        price_book=paper_price_book,
        instrument_key=instrument_key,
        broadcast=broadcast,
        last_payload=lambda: runtime_state.LAST_PAYLOAD,
    )
    bridge = DashboardBridge(
        state=lambda: {
            "symbol": runtime_state.MARKET_SELECTION.symbol,
            "futures_expiry": runtime_state.MARKET_SELECTION.futures_expiry,
            "use_smartapi": runtime_state.USE_SMARTAPI,
            "last_payload": runtime_state.LAST_PAYLOAD,
            "index_quotes": runtime_state.INDEX_QUOTES,
        },
        origin_allowed=origin_allowed,
        json_default=json_default,
        market_api=market_api,
        broker_futures_fetcher=broker_futures_fetcher,
        public_futures_fetcher=market_api.fetch_public_futures,
    )
    analytics = AnalyticsRuntime(
        symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        expiry=lambda: runtime_state.MARKET_SELECTION.expiry,
        data_source=lambda: runtime_state.MARKET_SELECTION.data_source,
        price_source=lambda: runtime_state.MARKET_SELECTION.price_source,
        futures_expiry=lambda: runtime_state.MARKET_SELECTION.futures_expiry,
        strikes_each_side=lambda: runtime_state.STRIKES_EACH_SIDE,
        activate_provider=activate_provider,
        resolve_default_expiry=resolve_default_expiry,
        apply_config=lambda _config: None,
        clear_capture=capture.clear,
        captured_payload=lambda: capture.payload,
        export_dashboard=capture.export,
        invoke_analytics=invoke_analytics,
        broker_adapters=(
            build_broker_market_adapters() if broker_services_enabled else None
        ),
        extra_chains=args.extra_chains,
        strict_expiry=args.strict_expiry,
        no_virtual_oi=args.no_virtual_oi,
    )
    runtime_state.FEEDS = build_feed_managers(
        default_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        main_loop=lambda: runtime_state.MAIN_LOOP,
        log=report,
    )
    symbol_switcher = SymbolSwitcher(
        current_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        current_expiry=lambda: runtime_state.MARKET_SELECTION.expiry,
        commit_selection=feed_manager._commit_symbol_selection,
        signal_refresh=runtime_state.SYMBOL_SWITCH_EVENT.set,
        live_feed_enabled=lambda: runtime_state.USE_SMARTAPI,
        live_feed_provider=lambda: runtime_state.LIVE_FEED_PROVIDER,
        restart_feed=feed_manager._restart_live_feed,
    )
    data_source_switcher = DataSourceSwitcher(
        valid_sources=lambda: provider_keys,
        current_source=lambda: runtime_state.MARKET_SELECTION.data_source,
        execution_gate=analytics.execution_gate,
        activate_provider=activate_provider,
        stop_feed=feed_manager._stop_active_broker_feed,
        commit_source=feed_manager._commit_data_source,
        supports_websocket=supports_websocket,
        restart_feed=feed_manager._restart_live_feed,
        current_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        current_expiry=lambda: runtime_state.MARKET_SELECTION.expiry,
        signal_refresh=runtime_state.SYMBOL_SWITCH_EVENT.set,
    )
    return CoreRuntime(
        broadcaster=broadcaster,
        broadcast=broadcast,
        paper_portfolio=paper_portfolio,
        bridge=bridge,
        analytics=analytics,
        symbol_switcher=symbol_switcher,
        data_source_switcher=data_source_switcher,
        payload_capture=capture,
    )
