"""Initialize mutable process state and paper-trading runtime services."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from execution.paper_trading import PaperTradingEngine
from operational_metrics import OperationalMetrics
from server.paper_portfolio import PaperPriceBook
from server.websocket_clients import WebSocketClientHub


@dataclass(frozen=True, slots=True)
class RuntimeBootstrap:
    paper_engine: Any
    paper_price_book: PaperPriceBook
    eod_trigger_time: time


def initialize_runtime_state(
    *,
    runtime_state: Any,
    instrument_key: Callable[..., str],
    environment: Mapping[str, str] = os.environ,
    now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    client_hub_factory: Callable[[], Any] = WebSocketClientHub,
    metrics_factory: Callable[..., Any] = OperationalMetrics,
    paper_engine_factory: Callable[[], Any] = PaperTradingEngine,
    price_book_factory: Callable[..., PaperPriceBook] = PaperPriceBook,
    event_factory: Callable[[], Any] = asyncio.Event,
    lock_factory: Callable[[], Any] = asyncio.Lock,
) -> RuntimeBootstrap:
    """Establish one coherent initial state before services are assembled."""
    runtime_state.DASHBOARD_CLIENTS = client_hub_factory()
    runtime_state.CONNECTED = runtime_state.DASHBOARD_CLIENTS.clients
    runtime_state.LAST_PAYLOAD = None
    runtime_state.LAST_PAYLOAD_AT = None
    runtime_state.LAST_SENT = None
    runtime_state.BASELINE_SEQ = 0
    runtime_state.BASELINE_ID = None
    runtime_state.PROCESS_STARTED_AT = now()
    runtime_state.LAST_HEALTH_LOG_STATE = None
    runtime_state.PIPELINE_STATUS = {
        "status": "STARTING",
        "reason": "Analytics pipeline has not completed yet",
        "startedAt": None,
        "lastSuccessAt": None,
        "elapsedSeconds": None,
    }
    runtime_state.METRICS = metrics_factory(started_at=runtime_state.PROCESS_STARTED_AT)
    runtime_state.LAST_FUNDS = None
    runtime_state.LAST_KNOWN_LEG_PRICES = {}
    runtime_state.LAST_PORTFOLIO_BROADCAST_TS = 0.0
    runtime_state.ALGO_STATUS_POLL_SECONDS = int(
        environment.get("runtime_state.ALGO_STATUS_POLL_SECONDS", "5")
    )
    runtime_state.LAST_ALGO_STATUS = None
    runtime_state.LAST_RECONCILIATION_ALERT = None
    runtime_state.LAST_LIVE_POSITIONS = None
    runtime_state.INDEX_QUOTES = {}
    runtime_state.SYMBOL_SWITCH_EVENT = event_factory()
    runtime_state.TICK_ACTIVITY_EVENT = event_factory()
    runtime_state.MARKET_STREAM_LOCK = lock_factory()

    paper_engine = paper_engine_factory()
    price_book = price_book_factory(
        runtime_state.LAST_KNOWN_LEG_PRICES,
        instrument_key,
    )
    return RuntimeBootstrap(
        paper_engine=paper_engine,
        paper_price_book=price_book,
        eod_trigger_time=time(15, 45),
    )
