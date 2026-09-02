"""Assembly and canonical broadcasting for dashboard WebSocket transport."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from server.websocket import DashboardWebSocketHandler
from server.websocket_handshake import WebSocketHandshakeSender
from server.websocket_messages import WebSocketMessageRouter
from server.websocket_query import WebSocketQueryController


class DashboardBroadcaster:
    """Version canonical snapshots and fan messages out to dashboard clients."""

    def __init__(
        self,
        *,
        runtime_state: Any,
        encode: Callable[[Any], str],
        report: Callable[[str], Any],
    ) -> None:
        self._state = runtime_state
        self._encode = encode
        self._report = report
        self._reported_missing_baseline = False

    async def broadcast(self, message: Any) -> None:
        if isinstance(message, dict) and message.get("type") == "full":
            self._state.BASELINE_SEQ += 1
            payload = message.get("payload") or {}
            self._state.BASELINE_ID = (
                f"{payload.get('symbol', '')}:{payload.get('expiry', '')}:"
                f"{self._state.BASELINE_SEQ}"
            )
            message = {**message, "version": self._state.BASELINE_ID}
            self._reported_missing_baseline = False
        elif isinstance(message, dict) and message.get("type") == "delta":
            if self._state.BASELINE_ID is None:
                if not self._reported_missing_baseline:
                    self._report(
                        "[ws] dropping deltas until a full-snapshot baseline "
                        "is established"
                    )
                    self._reported_missing_baseline = True
                return
            message = {**message, "baseVersion": self._state.BASELINE_ID}
        await self._state.DASHBOARD_CLIENTS.broadcast(
            self._encode(message),
            on_error=lambda error: self._report(f"[ws] Error broadcasting: {error}"),
        )


@dataclass(frozen=True, slots=True)
class DashboardTransport:
    handshake: WebSocketHandshakeSender
    message_router: WebSocketMessageRouter
    query_controller: WebSocketQueryController
    handler: DashboardWebSocketHandler


def build_dashboard_transport(
    *,
    runtime_state: Any,
    encode: Callable[[Any], str],
    decode: Callable[[str], Any],
    origin_allowed: Callable[..., bool],
    place_order: Callable[[dict], Awaitable[Any]],
    cancel_order: Callable[[str], bool],
    portfolio_broadcast: Callable[..., Awaitable[Any]],
    build_current_prices: Callable[[Any], dict],
    start_funds_polling: Callable[[], Any],
    stop_funds_polling: Callable[[], Any],
    control_feed: Callable[[bool], dict],
    broadcast_control: Callable[[dict], Awaitable[Any]],
    feed_control_status: Callable[[], dict],
    switch_symbol: Callable[..., Any],
    switch_data_source: Callable[..., Awaitable[Any]],
    build_algo_status: Callable[[], dict],
    paper_snapshot: Callable[[], tuple[dict, list]],
    logger: Any,
) -> DashboardTransport:
    handshake = WebSocketHandshakeSender(
        encode=encode,
        market_lock=runtime_state.MARKET_STREAM_LOCK,
        market_payload=lambda: runtime_state.LAST_PAYLOAD,
        baseline_version=lambda: runtime_state.BASELINE_ID,
        index_quotes=lambda: runtime_state.INDEX_QUOTES,
        pipeline_status=lambda: runtime_state.PIPELINE_STATUS,
        feed_control=feed_control_status,
        funds=lambda: runtime_state.LAST_FUNDS,
        algo_status=lambda: (
            runtime_state.LAST_ALGO_STATUS
            if runtime_state.LAST_ALGO_STATUS is not None
            else build_algo_status()
        ),
        reconciliation_alert=lambda: runtime_state.LAST_RECONCILIATION_ALERT,
        paper_snapshot=paper_snapshot,
    )
    router = WebSocketMessageRouter(
        place_order=place_order,
        cancel_order=cancel_order,
        broadcast_portfolio=portfolio_broadcast,
        build_current_prices=build_current_prices,
        last_payload=lambda: runtime_state.LAST_PAYLOAD,
        start_funds_polling=start_funds_polling,
        stop_funds_polling=stop_funds_polling,
        control_feed=control_feed,
        broadcast_control=broadcast_control,
    )
    query_controller = WebSocketQueryController(
        current_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        switch_symbol=switch_symbol,
        switch_data_source=switch_data_source,
        current_price_source=lambda: runtime_state.MARKET_SELECTION.price_source,
        set_price_source=runtime_state.MARKET_SELECTION.select_price_source,
        current_futures_expiry=lambda: runtime_state.MARKET_SELECTION.futures_expiry,
        set_futures_expiry=runtime_state.MARKET_SELECTION.select_futures_expiry,
        invalidate_market_baseline=runtime_state.invalidate_market_baseline,
    )
    handler = DashboardWebSocketHandler(
        origin_allowed=origin_allowed,
        clients=runtime_state.DASHBOARD_CLIENTS,
        connected_count=lambda: len(runtime_state.CONNECTED),
        metrics=runtime_state.METRICS,
        query_controller=query_controller,
        send_handshake=handshake.send,
        has_market_payload=lambda: runtime_state.LAST_PAYLOAD is not None,
        decode=decode,
        dispatch_message=router.dispatch,
        symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        expiry=lambda: runtime_state.MARKET_SELECTION.expiry,
        logger=logger,
    )
    return DashboardTransport(handshake, router, query_controller, handler)
