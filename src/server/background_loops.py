"""Periodic background loops extracted from ws_server_live.

Mirrors the injected-callable pattern already used by BrokerFeedManager
(server/feed_manager.py) and LiveOrderGateway (server/order_gateway.py):
loop *mechanics* live here, fully unit-testable with fake callables.
Runtime state that other parts of ws_server_live already read directly
(LAST_FUNDS, LAST_LIVE_POSITIONS, LAST_ALGO_STATUS, INDEX_QUOTES,
_NODE_SESSION — e.g. the handshake snapshot and shutdown cleanup) is NOT
owned here. It stays exactly where it already lives (ws_server_live module
globals), and is threaded through via get_*/set_* seams — same reasoning
server/feed_manager.py's own docstring gives for keeping feed state as
"legacy module globals because tests seam through them".

This is a pure refactor: no behavior change relative to the functions it
replaces (index_quote_loop, _funds_poll_body/start_funds_polling/
stop_funds_polling, reconcile_loop, algo_status_loop, _get_node_session/
_post_to_node).
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional, Sequence


class IndexQuoteLoop:
    """Periodic ticker-strip quotes for the non-active indices.

    Recomputes the "other symbols" list every cycle rather than once —
    switch_symbol() can change the active symbol mid-loop, and a list
    captured once would keep excluding the OLD active symbol forever
    while never refreshing the new one.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        symbols: Sequence[str],
        active_symbol: Callable[[], str],
        get_spot_quote: Callable[[str], Optional[dict]],
        broadcast: Callable[[dict], Awaitable[None]],
        index_quotes: dict,  # mutated in place — this IS ws_server_live's INDEX_QUOTES
        poll_seconds: float,
        report: Callable[[str], None] = print,
    ) -> None:
        self._enabled = enabled
        self._symbols = symbols
        self._active_symbol = active_symbol
        self._get_spot_quote = get_spot_quote
        self._broadcast = broadcast
        self._index_quotes = index_quotes
        self._poll_seconds = poll_seconds
        self._report = report

    async def run(self) -> None:
        if not self._enabled:
            return
        while True:
            await self.tick()
            await asyncio.sleep(self._poll_seconds)

    async def tick(self) -> None:
        """One polling cycle. Public/awaitable directly for tests."""
        active = self._active_symbol()
        others = [s for s in self._symbols if s != active]
        updates: dict = {}
        for sym in others:
            try:
                raw = await asyncio.to_thread(self._get_spot_quote, sym)
                if raw and raw.get("ltp") is not None:
                    ltp = float(raw["ltp"])
                    close = raw.get("close")
                    chg_pct = None
                    if close not in (None, 0, 0.0):
                        close = float(close)
                        chg_pct = ((ltp - close) / close) * 100.0
                    updates[sym] = {"spot": ltp, "spotChgPct": chg_pct}
            except Exception as exc:
                self._report(f"[index-quote] {sym} broker quote failed: {exc}")
        if updates:
            self._index_quotes.update(updates)
            await self._broadcast({"type": "indexQuotes", "payload": updates})
            for sym, quote in updates.items():
                self._report(
                    f"[index-quote] {sym} spot={quote.get('spot')} "
                    f"chg%={quote.get('spotChgPct')}"
                )


class FundsPoller:
    """Start/stop-able funds polling.

    Deliberately NOT gated on LIVE_TRADING_ENABLED — that flag guards real
    ORDERS (restart-only by design), but reading account balance moves no
    money and carries no execution risk. start()/stop() are driven by the
    {"type":"toggle_live_mode"} WS message instead — flipping the
    dashboard's LIVE pill controls this over the live socket, no restart,
    same pattern as switch_symbol().
    """

    def __init__(
        self,
        *,
        get_funds: Callable[[], dict],
        broadcast: Callable[[dict], Awaitable[None]],
        set_last_funds: Callable[[Optional[dict]], None],
        poll_seconds: float,
        spawn_task: Callable[[Awaitable, str], "asyncio.Task"],
        report: Callable[[str], None] = print,
    ) -> None:
        self._get_funds = get_funds
        self._broadcast = broadcast
        self._set_last_funds = set_last_funds
        self._poll_seconds = poll_seconds
        self._spawn_task = spawn_task
        self._report = report
        self._task: Optional["asyncio.Task"] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Idempotent — a second toggle-on while already running is a no-op,
        not a duplicate poller."""
        if self.running:
            return
        self._report("[funds] starting funds polling (live mode toggled on)")
        self._task = self._spawn_task(self._poll_body(), "funds_poll")

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
            self._report("[funds] stopped funds polling (live mode toggled off)")
        # Clear last-known funds too, not just stop broadcasting — a client
        # that reconnects while polling is off must not be handed a
        # possibly-stale real-money figure in the handshake snapshot.
        self._set_last_funds(None)

    async def _poll_body(self) -> None:
        """One polling cycle, repeated until cancelled by stop()."""
        while True:
            try:
                # get_funds() makes a real blocking HTTP call (and may
                # trigger a re-login) — offload like every other blocking
                # call here, never inline on the event loop.
                funds = await asyncio.to_thread(self._get_funds)
                self._set_last_funds(funds)
                await self._broadcast({"type": "funds", "payload": funds})
                self._report(
                    f"[funds] available={funds.get('available_margin')} "
                    f"utilised={funds.get('utilised_margin')}"
                )
            except Exception as e:
                # A failed poll (session hiccup, rate limit, network blip)
                # never takes down the loop — the frontend keeps showing the
                # last good value (or "n/a") until the next cycle succeeds.
                self._report(
                    f"[funds] poll failed (will retry in {self._poll_seconds}s): {e}"
                )
            await asyncio.sleep(self._poll_seconds)


class ReconciliationLoop:
    """Periodic position reconciliation — the safety net for drift
    unrelated to this app's own order flow (a position closed manually in
    the broker app, a fill that landed without this process seeing it)."""

    def __init__(
        self,
        *,
        get_order_book: Callable[[], object],
        get_positions: Callable[[], object],
        reconciler,
        lot_sizes,
        set_last_positions: Callable[[object], None],
        broadcast_alert: Callable[[object, str], Awaitable[None]],
        poll_seconds: float,
        report: Callable[[str], None] = print,
    ) -> None:
        self._get_order_book = get_order_book
        self._get_positions = get_positions
        self._reconciler = reconciler
        self._lot_sizes = lot_sizes
        self._set_last_positions = set_last_positions
        self._broadcast_alert = broadcast_alert
        self._poll_seconds = poll_seconds
        self._report = report

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self._poll_seconds)

    async def tick(self) -> None:
        try:
            orders = await asyncio.to_thread(self._get_order_book)
            positions = await asyncio.to_thread(self._get_positions)
            self._set_last_positions(positions)
            result = self._reconciler.check(orders, positions, self._lot_sizes)
            if result.clean:
                self._report("[position_reconciler] periodic check: clean")
            else:
                self._report(
                    f"[position_reconciler] periodic check: "
                    f"{len(result.mismatches)} mismatch(es), "
                    f"{len(result.unparseable_symbols)} unparseable"
                )
                await self._broadcast_alert(result, "periodic")
        except Exception as e:
            # Same defensive posture as every other periodic loop — skip
            # this cycle, never take down the loop.
            self._report(
                f"[position_reconciler] periodic check failed "
                f"(will retry in {self._poll_seconds}s): {e}"
            )


class AlgoStatusLoop:
    """Periodic algoStatus broadcast. Runs unconditionally (not gated on
    LIVE_TRADING_ENABLED) so the panel always shows an accurate picture —
    including confirming live trading/auto-execution are OFF, not just when
    they're armed."""

    def __init__(
        self,
        *,
        build_status: Callable[[], dict],
        broadcast: Callable[[dict], Awaitable[None]],
        set_last_status: Callable[[dict], None],
        poll_seconds: float,
        report: Callable[[str], None] = print,
    ) -> None:
        self._build_status = build_status
        self._broadcast = broadcast
        self._set_last_status = set_last_status
        self._poll_seconds = poll_seconds
        self._report = report

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self._poll_seconds)

    async def tick(self) -> None:
        try:
            status = self._build_status()
            self._set_last_status(status)
            await self._broadcast({"type": "algoStatus", "payload": status})
        except Exception as e:
            self._report(
                f"[algo-status] poll failed (will retry in {self._poll_seconds}s): {e}"
            )


class NodeRelay:
    """Optional fire-and-forget relay of every canonical payload to a local
    Node bridge process. A no-op (post() returns immediately) unless
    enabled — same as the USE_RELAY-gated behavior it replaces."""

    def __init__(
        self,
        *,
        enabled: bool,
        url: str = "http://localhost:4000/api/broadcast",
        timeout_seconds: float = 2.0,
        report: Callable[[str], None] = print,
    ) -> None:
        self._enabled = enabled
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._report = report
        self._session = None

    async def _get_session(self):
        import aiohttp

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def post(self, payload: dict) -> None:
        if not self._enabled:
            return
        import aiohttp

        try:
            session = await self._get_session()
            async with session.post(
                self._url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
            ) as resp:
                await resp.read()
        except Exception as e:
            self._report(f"[node-relay] failed: {e}")

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
