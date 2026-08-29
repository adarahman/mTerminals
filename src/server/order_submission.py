"""Manual and automated order submission orchestration."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from server.order_intent import parse_order_intent, validate_order_intent


class OrderSubmissionService:
    """Route validated intents through live or paper execution."""

    def __init__(
        self,
        *,
        live_orders,
        paper_engine,
        price_book,
        portfolio_broadcast: Callable[[dict], Awaitable[None]],
        reconciliation_alert: Callable[[object, str], Awaitable[None]],
        last_payload: Callable[[], dict | None],
        instrument_key: Callable[..., str],
        report: Callable[[str], None] = print,
        client_order_id: Callable[[], str] | None = None,
    ) -> None:
        self._live_orders = live_orders
        self._paper_engine = paper_engine
        self._price_book = price_book
        self._portfolio_broadcast = portfolio_broadcast
        self._reconciliation_alert = reconciliation_alert
        self._last_payload = last_payload
        self._instrument_key = instrument_key
        self._report = report
        self._client_order_id = client_order_id or (
            lambda: "a" + uuid.uuid4().hex[:19]
        )

    async def handle(self, payload, _live_gate_acquired: bool = False) -> dict:
        intent = parse_order_intent(payload)
        validation_reason = validate_order_intent(intent)
        current_prices = self._price_book.build(self._last_payload())
        if validation_reason:
            self._report(f"[order] REJECTED malformed intent: {validation_reason}")
            await self._portfolio_broadcast(current_prices)
            return {"status": "rejected", "reason": validation_reason}

        if intent.wants_live and not _live_gate_acquired:
            async with self._live_orders.order_gate():
                return await self.handle(payload, _live_gate_acquired=True)

        if intent.wants_live:
            return await self._live_orders.place_live_order(
                intent,
                current_prices,
                broadcast_portfolio=self._portfolio_broadcast,
                broadcast_alert=self._reconciliation_alert,
            )

        key = self._instrument_key(
            intent.symbol,
            intent.expiry,
            intent.strike,
            intent.instrument_type,
        )
        order = self._paper_engine.place_order(
            intent.symbol,
            intent.side,
            intent.qty_lots,
            instrument_type=intent.instrument_type,
            expiry=intent.expiry,
            strike=intent.strike,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
            current_ltp=current_prices.get(key),
            client_order_id=intent.client_order_id,
        )
        displayed_price = (
            order.fill_price if order.fill_price is not None else intent.limit_price
        )
        self._report(
            f"[paper-trading] {order.status}: {intent.symbol} {intent.side} "
            f"{intent.qty_lots} lot(s) {intent.instrument_type} {intent.expiry} "
            f"{intent.strike} "
            f"@ {displayed_price}"
            + (f" — {order.reject_reason}" if order.reject_reason else "")
        )
        await self._portfolio_broadcast(current_prices)
        return {
            "status": order.status,
            "reason": order.reject_reason,
            "order_id": getattr(order, "id", None),
            "client_order_id": getattr(
                order, "client_order_id", intent.client_order_id
            ),
        }

    async def submit_auto(
        self,
        symbol,
        instrument_type,
        expiry,
        strike,
        side,
        qty_lots,
    ) -> dict:
        result = await self.handle(
            {
                "symbol": symbol,
                "instrument_type": instrument_type,
                "expiry": expiry,
                "strike": strike,
                "side": side,
                "order_type": "MARKET",
                "qty_lots": qty_lots,
                "client_order_id": self._client_order_id(),
                "live": True,
                "confirmed": True,
            }
        )
        status = (result or {}).get("status")
        if status != "placed":
            reason = (result or {}).get("reason") or (
                f"unexpected status {status!r} from order submission"
            )
            raise RuntimeError(reason)
        return result
