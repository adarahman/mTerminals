"""Server-side gate for real-money order submission.

Owns every mechanical check between "the dashboard asked for a live order"
and "the broker adapter's place_order() was called":

- intent parsing/coercion and validation (the browser is untrusted input);
- LIVE_TRADING_ENABLED (restart-only master switch, deliberately not
  toggleable mid-session);
- the kill-switch file (instant, checked on every order, no restart);
- per-order lot ceiling and the sliding per-minute rate cap;
- idempotent submission keyed by client_order_id (collapses retries; the
  broker order tag carries the same identity for post-hoc recovery);
- a per-event-loop asyncio gate serializing the position read, the
  projected-exposure check, and the submission — locking only place_order
  leaves a TOCTOU window where two requests both observe the same position
  book and independently clear the exposure cap.

Rejection policy is fail-closed: any malformed field, unresolvable
instrument, or unverified lot size rejects the live order. Nothing here ever
guesses quantities — a wrong lot size on the live path means submitting the
WRONG QUANTITY to the real exchange, so there is exactly one source of truth
(paper_trading.LOT_SIZES) and no silent fallback.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Callable, Optional

from market.instruments import instrument_from_execution_resolution
from risk.account_guard import pnl_from_positions, projected_open_lots_from_positions
from server.order_intent import OrderIntent


class LiveOrderGateway:
    """Mechanics of live submission; flow/I-O (broadcasts) stay with the
    coordinator via the injected callbacks."""

    def __init__(
        self,
        *,
        enabled: bool,
        kill_switch_file: str,
        max_lots_per_order: int,
        max_orders_per_minute: int,
        lot_sizes: dict,
        account_guard,
        position_reconciler,
        resolve_token: Callable,
        place_order: Callable,
        get_positions: Callable,
        get_order_book: Callable,
        order_store,
        results_max: int = 500,
        log: Callable[[str], None] = print,
    ) -> None:
        self.enabled = enabled
        self.kill_switch_file = kill_switch_file
        self.max_lots_per_order = max_lots_per_order
        self.max_orders_per_minute = max_orders_per_minute
        self._lot_sizes = lot_sizes
        self._guard = account_guard
        self._reconciler = position_reconciler
        self._resolve_token = resolve_token
        self._place_order = place_order
        self._get_positions = get_positions
        self._get_order_book = get_order_book
        self._store = order_store
        self._results_max = results_max
        self._log = log
        self._order_timestamps: list = []  # sliding rate window, main-thread only
        self._submit_lock = threading.Lock()
        self._results: dict = {}
        self._gate: Optional[asyncio.Lock] = None
        self._gate_loop = None

    # ── primitives ───────────────────────────────────────────────────
    def kill_switch_active(self) -> bool:
        return os.path.exists(self.kill_switch_file)

    def rate_limit_allows(self) -> bool:
        """Sliding 60s window cap, independent of the broker's own quota —
        a tighter self-imposed ceiling limiting the blast radius of a
        runaway client/bug, not an attempt to maximize throughput."""
        now = time.monotonic()
        cutoff = now - 60
        timestamps = self._order_timestamps
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)
        if len(timestamps) >= self.max_orders_per_minute:
            return False
        timestamps.append(now)
        return True

    def completed_order(self, client_order_id):
        """A previously completed live submission, if any."""
        with self._submit_lock:
            cached = self._results.get(client_order_id)
            if cached is not None:
                return cached
            persisted = self._store.get(client_order_id)
            if persisted is not None:
                self._results[client_order_id] = persisted
            return persisted

    def submit_idempotent(self, client_order_id, *args, **kwargs):
        """Serialize submissions and collapse retries by client ID."""
        with self._submit_lock:
            existing = self._results.get(client_order_id)
            if existing is not None:
                return existing, True
            order_id = self._place_order(*args, **kwargs, order_tag=client_order_id)
            order_id = self._store.record(client_order_id, order_id)
            self._results[client_order_id] = order_id
            while len(self._results) > self._results_max:
                self._results.pop(next(iter(self._results)))
            return order_id, False

    def order_gate(self) -> asyncio.Lock:
        """One live-order critical section per event loop. Tests create
        multiple short-lived loops, so the lock is recreated when the
        active loop changes."""
        loop = asyncio.get_running_loop()
        if self._gate is None or self._gate_loop is not loop:
            self._gate = asyncio.Lock()
            self._gate_loop = loop
        return self._gate

    # ── the live path ────────────────────────────────────────────────
    def _identity_check(self, client_order_id):
        """Returns (duplicate_order_id_or_None, rejection_reason_or_None)."""
        if (
            not isinstance(client_order_id, str)
            or not 8 <= len(client_order_id) <= 20
            or not client_order_id.isalnum()
        ):
            return None, "live orders require an 8-20 character alphanumeric client_order_id"
        return self.completed_order(client_order_id), None

    def _policy_rejection(self, intent: OrderIntent) -> Optional[str]:
        if not self.enabled:
            return "live trading disabled on server"
        if self.kill_switch_active():
            return "live trading kill switch active"
        if not 1 <= intent.qty_lots <= self.max_lots_per_order:
            return (
                f"qty_lots {intent.qty_lots} outside allowed range "
                f"(1-{self.max_lots_per_order})"
            )
        if not self.rate_limit_allows():
            return f"rate limit exceeded ({self.max_orders_per_minute}/min)"
        if intent.symbol not in self._lot_sizes:
            # No verified lot size = refusing to guess on a live order. Add
            # the symbol to paper_trading.LOT_SIZES (after confirming
            # against NSE's current circular) before trading it live.
            return f"no verified lot size for {intent.symbol} — refusing to guess on a live order"
        tripped, trip_reason = self._guard.is_tripped()
        if tripped:
            return f"account risk guard tripped: {trip_reason}"
        return None

    async def place_live_order(
        self,
        intent: OrderIntent,
        current_prices: dict,
        *,
        broadcast_portfolio: Callable,
        broadcast_alert: Callable,
    ) -> dict:
        """Full live pre-trade check chain + idempotent submission.

        Returns a {"status": ...} dict on EVERY path so callers (notably
        the auto-executor bridge) can tell a rejection from a placement."""
        symbol, side, qty_lots = intent.symbol, intent.side, intent.qty_lots
        client_order_id = intent.client_order_id

        duplicate, reason = self._identity_check(client_order_id)
        if duplicate is not None:
            await broadcast_portfolio(current_prices)
            return {
                "status": "placed",
                "order_id": duplicate,
                "client_order_id": client_order_id,
                "duplicate": True,
            }
        if reason is None:
            reason = self._policy_rejection(intent)
        if reason is not None:
            self._log(
                f"[live-trading] REJECTED: {reason} — {symbol} {side} {qty_lots} lot(s)"
            )
            await broadcast_portfolio(current_prices)
            return {"status": "rejected", "reason": reason}

        resolved = self._resolve_token(
            intent.symbol, intent.instrument_type, intent.expiry, intent.strike
        )
        if resolved is None:
            reason = (
                f"could not resolve instrument token for {intent.symbol} "
                f"{intent.expiry} {intent.strike}{intent.instrument_type}"
            )
            self._log(f"[live-trading] REJECTED: {reason}")
            await broadcast_portfolio(current_prices)
            return {"status": "rejected", "reason": reason}

        try:
            instrument = instrument_from_execution_resolution(
                intent.symbol,
                intent.instrument_type,
                intent.expiry,
                intent.strike,
                resolved,
            )
        except ValueError as exc:
            reason = f"invalid instrument resolution: {exc}"
            self._log(f"[live-trading] REJECTED: {reason}")
            await broadcast_portfolio(current_prices)
            return {"status": "rejected", "reason": reason}

        exchange = instrument.exchange.value
        tradingsymbol = instrument.trading_symbol
        symboltoken = instrument.token
        # Valid key guaranteed here — unknown symbols were rejected above.
        quantity = qty_lots * self._lot_sizes[symbol]
        transaction_type = "BUY" if side == "BUY" else "SELL"

        # Exposure after applying this exact signed order: permits
        # risk-reducing closes while failing closed on an incomplete
        # position book. The caller's gate keeps this read/check atomic
        # with the submission below.
        try:
            live_positions = await asyncio.to_thread(self._get_positions)
            projected_open_lots = projected_open_lots_from_positions(
                live_positions,
                self._lot_sizes,
                tradingsymbol,
                transaction_type,
                quantity,
            )
        except Exception as e:
            self._log(
                f"[account_guard] could not fetch position book for exposure check: {e}"
            )
            projected_open_lots = None
        allowed, exposure_reason = self._guard.check_new_order(0, projected_open_lots)
        if not allowed:
            self._log(
                f"[live-trading] REJECTED: {exposure_reason} — {symbol} {side} {qty_lots} lot(s)"
            )
            await broadcast_portfolio(current_prices)
            return {"status": "rejected", "reason": exposure_reason}

        try:
            order_id, duplicate = await asyncio.to_thread(
                self.submit_idempotent,
                client_order_id,
                tradingsymbol,
                symboltoken,
                exchange,
                transaction_type,
                quantity,
                order_type=intent.order_type,
                price=intent.limit_price or 0.0,
            )
            self._log(
                f"[live-trading] PLACED: {tradingsymbol} {transaction_type} "
                f"{quantity} qty (order_id={order_id})"
            )
            result = {
                "status": "placed",
                "order_id": order_id,
                "client_order_id": client_order_id,
                "duplicate": duplicate,
            }
        except Exception as e:
            self._log(
                f"[live-trading] FAILED: {tradingsymbol} {transaction_type} "
                f"{quantity} — {e}"
            )
            result = {"status": "failed", "reason": str(e)}
        finally:
            await self._post_fill(broadcast_portfolio, broadcast_alert, current_prices)
        return result

    async def _post_fill(self, broadcast_portfolio, broadcast_alert, current_prices):
        """Refresh the guard's daily P&L, run a fast post-fill
        reconciliation (the periodic sweep is the real safety net; this is
        the quicker check right after our own action), re-broadcast."""
        post_fill_positions = None
        try:
            post_fill_positions = await asyncio.to_thread(self._get_positions)
            self._guard.update_pnl(pnl_from_positions(post_fill_positions))
        except Exception as e:
            self._log(f"[account_guard] could not refresh daily P&L after order: {e}")
        if post_fill_positions is not None:
            try:
                post_fill_orders = await asyncio.to_thread(self._get_order_book)
                post_fill_result = self._reconciler.check(
                    post_fill_orders, post_fill_positions, self._lot_sizes
                )
                await broadcast_alert(post_fill_result, source="post_fill")
            except Exception as e:
                self._log(f"[position_reconciler] could not run post-fill check: {e}")
        await broadcast_portfolio(current_prices)
