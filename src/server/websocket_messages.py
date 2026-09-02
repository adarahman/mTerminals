"""Inbound dashboard WebSocket control-message routing."""
from __future__ import annotations

import traceback
from collections.abc import Awaitable, Callable, Mapping
from typing import Any


class WebSocketMessageRouter:
    """Dispatch control messages through injected application operations."""

    def __init__(
        self,
        *,
        place_order: Callable[[dict], Awaitable[Any]],
        cancel_order: Callable[[str], bool],
        broadcast_portfolio: Callable[[dict], Awaitable[Any]],
        build_current_prices: Callable[[Any], dict],
        last_payload: Callable[[], Any],
        start_funds_polling: Callable[[], Any],
        stop_funds_polling: Callable[[], Any],
        control_feed: Callable[[bool], Mapping[str, Any]],
        broadcast_control: Callable[[dict], Awaitable[Any]],
    ):
        self._place_order = place_order
        self._cancel_order = cancel_order
        self._broadcast_portfolio = broadcast_portfolio
        self._build_current_prices = build_current_prices
        self._last_payload = last_payload
        self._start_funds_polling = start_funds_polling
        self._stop_funds_polling = stop_funds_polling
        self._control_feed = control_feed
        self._broadcast_control = broadcast_control

    async def dispatch(self, data: Mapping[str, Any] | Any) -> None:
        if not isinstance(data, Mapping):
            return
        message_type = data.get("type")
        payload = data.get("payload") or {}

        if message_type == "place_order":
            try:
                await self._place_order(payload)
            except Exception as exc:
                print(f"[paper-trading] place_order FAILED: {exc}", flush=True)
                traceback.print_exc()
            return

        if message_type == "cancel_order":
            try:
                order_id = payload.get("order_id")
                if order_id:
                    success = self._cancel_order(order_id)
                    print(
                        f"[paper-trading] CANCEL {order_id}: "
                        f"{'success' if success else 'failed'}",
                        flush=True,
                    )
                    prices = self._build_current_prices(self._last_payload())
                    await self._broadcast_portfolio(prices)
            except Exception as exc:
                print(f"[paper-trading] cancel_order FAILED: {exc}", flush=True)
            return

        if message_type == "toggle_live_mode":
            if bool(payload.get("enabled")):
                self._start_funds_polling()
            else:
                self._stop_funds_polling()
            return

        if message_type == "control_feed":
            result = self._control_feed(bool(payload.get("enabled")))
            await self._broadcast_control({"type": "feedControl", "payload": result})
