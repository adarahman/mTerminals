"""
shoonya_ws_client.py
=====================
Real-time tick streaming via Shoonya/Finvasia's Noren WebSocket feed —
the Shoonya analog of brokers/smartapi_ws_client.py and
brokers/upstox_ws_client.py.

Normalizes every incoming tick into the SAME wire schema those two
already produce (token, last_traded_price, open_interest,
volume_trade_for_the_day, closed_price, average_traded_price) so it
feeds straight into tick_pipeline.TickAggregator with zero
changes to that class — same reasoning upstox_ws_client.py's module
docstring gives for its own normalization.

Noren's feed is STATEFUL, unlike SmartAPI's/Upstox's: the first message
per token ('t': 'tk') carries every field; every later message for that
token ('t': 'tf') carries ONLY the fields that changed since the last
one (confirmed against ShoonyaApi-Py's own docs — see the 'tk'/'tf'
example in its Websocket API section: a 'tf' commonly arrives as just
{'t': 'tf', 'e': 'NSE', 'tk': '11630', 'lp': '118.60'}). Treating each
message as self-contained the way SmartAPI/Upstox ticks already are
would silently blank out OI/volume on every partial update. This module
keeps a per-token last-known-state cache and MERGES each incoming
message into it before emitting, so on_tick() always receives a
complete-as-of-now snapshot in the same shape SmartTickStream/
UpstoxTickStream already hand TickAggregator.

Subscribe format: Noren identifies instruments as "EXCH|TOKEN" strings
(e.g. "NSE|26000", "NFO|48029") — confirmed in ShoonyaApi-Py's own
example_market.py (api.subscribe('NSE|11630')). There's no separate
exchangeType integer the way SmartAPI's EXCHANGE_TYPE needs; the
exchange is embedded directly in the subscribe string, so this module's
subscribe()/unsubscribe() just take those pre-built strings — same
shape UpstoxTickStream.subscribe() takes instrument_key strings.

Usage — mirrors smartapi_ws_client.py's SmartTickStream:

    from brokers.shoonya.websocket import ShoonyaTickStream

    def on_tick(tick):
        print(tick)  # {token, last_traded_price, open_interest, ...}

    stream = ShoonyaTickStream(on_tick=on_tick)
    stream.connect()
    threading.Thread(target=stream.run_forever_with_reconnect, daemon=True).start()
    time.sleep(2)  # let the WS connection establish
    stream.subscribe(["NSE|26000", "NFO|48029"])

No separate pip install needed: unlike upstox_ws_client.py's optional
upstox-python-sdk, the Noren websocket path ships inside the same
ShoonyaApi-py checkout brokers/shoonya_client.py already depends on
(see setup_shoonya.sh) — nothing extra to install for this module.

NOT WIRED UP: order/trade push updates (Noren's 'om' messages, sent on
the same socket once subscribe_orders() is called) are accepted here
via _handle_order_update() but discarded — execution/order-book state
in this codebase is polled through shoonya_client.get_order_book(), not
pushed. Wiring push-based order updates in would be a separate change.
"""
from __future__ import annotations

import inspect
import logging
import threading
import time

from brokers.shoonya.client import _session

logger = logging.getLogger(__name__)

# Noren sends these as strings (confirmed against ShoonyaApi-Py's docs'
# 'tk'/'tf' examples); this module's consumers (TickAggregator) expect
# floats, matching SmartTickStream's/UpstoxTickStream's normalized shape.
_NUMERIC_FIELDS = {
    "lp": "last_traded_price",
    "c": "closed_price",
    "ap": "average_traded_price",
    "v": "volume_trade_for_the_day",
    "oi": "open_interest",
}

# Message types this module treats as ticks. 't'/'d' touchline/depth,
# each in ack ('k') or update ('f') stage — see ShoonyaApi-Py's own
# Websocket API docs. Depth-only fields (bid/ask ladders) aren't in
# _NUMERIC_FIELDS above and are simply ignored by _merge_and_normalize.
_TICK_MESSAGE_TYPES = {"tk", "tf", "dk", "df"}


class ShoonyaTickStream:
    """Wraps NorenApi.start_websocket() with the same public surface
    SmartTickStream/UpstoxTickStream expose: connect(), subscribe(),
    unsubscribe(), run_forever(), run_forever_with_reconnect(), close(),
    and a `_connected` threading.Event — ws_server_live.py's health
    endpoint already knows how to read this attribute off whichever
    stream object is active."""

    def __init__(self, on_tick=None, on_error=None, on_close=None):
        self._on_tick_cb = on_tick
        self._on_error_cb = on_error
        self._on_close_cb = on_close

        self._connected = threading.Event()
        self._closing = False

        # Desired subscription STATE (mirrors SmartTickStream's/
        # UpstoxTickStream's self._desired) — replayed in full on every
        # (re)connect via _handle_open(), since start_websocket() gives
        # no documented guarantee subscriptions survive a reconnect.
        self._desired = set()
        self._desired_lock = threading.Lock()

        # Per-instrument last-known-state cache — required because
        # Noren's 'tf' updates are partial (see module docstring).
        self._last_state = {}
        self._state_lock = threading.Lock()

    def connect(self):
        """(Re)starts the underlying Noren websocket. Like
        UpstoxTickStream.connect() (and unlike SmartTickStream.connect()),
        this does not block — NorenApi.start_websocket() spins up its own
        thread internally (confirmed by ShoonyaApi-Py's own example
        scripts, which call it and then continue straight into an
        interactive loop without waiting)."""
        _session.ensure_session()
        api = _session.api

        self._closing = False

        # start_websocket()'s exact kwarg set varies slightly across
        # ShoonyaApi-py forks (some expose socket_close_callback /
        # socket_error_callback, some don't — undocumented either way in
        # the official docs). Inspect the live signature and only pass
        # what this install actually supports rather than hard-coding
        # kwargs that could raise TypeError on a different checkout.
        candidate_kwargs = {
            "subscribe_callback": self._handle_tick,
            "order_update_callback": self._handle_order_update,
            "socket_open_callback": self._handle_open,
            "socket_close_callback": self._handle_close,
            "socket_error_callback": self._handle_error,
        }
        try:
            supported = set(inspect.signature(api.start_websocket).parameters)
        except (TypeError, ValueError):
            supported = set(candidate_kwargs)  # best effort if introspection fails
        kwargs = {k: v for k, v in candidate_kwargs.items() if k in supported}

        api.start_websocket(**kwargs)

    def _handle_open(self):
        logger.info("[shoonya_ws] Connected")
        self._connected.set()
        with self._desired_lock:
            snapshot = sorted(self._desired)
        if snapshot:
            self._do_subscribe(snapshot)

    def _handle_order_update(self, message):
        # See module docstring's "NOT WIRED UP" note — order pushes are
        # intentionally discarded here.
        pass

    def _merge_and_normalize(self, tick):
        exch = tick.get("e")
        token = tick.get("tk")
        if token is None:
            return None
        key = f"{exch}|{token}" if exch else str(token)

        with self._state_lock:
            state = self._last_state.setdefault(key, {})
            state.update(tick)
            merged = dict(state)

        # Keyed as "EXCH|TOKEN" (not the bare numeric token) so this
        # matches the subscribe-string keys ws_server_live.py's
        # _resolve_shoonya_chain_tokens() builds instrument_meta with —
        # a bare token isn't guaranteed unique across exchanges the way
        # SmartAPI's per-exchangeType tokens are.
        out = {"token": key}
        for src, dest in _NUMERIC_FIELDS.items():
            val = merged.get(src)
            if val is None:
                continue
            try:
                out[dest] = float(val)
            except (TypeError, ValueError):
                continue
        # last_traded_price is the one field downstream code (TickAggregator)
        # treats as required — skip emitting if it hasn't arrived yet (only
        # possible if a 'tf' somehow beat the initial 'tk' snapshot).
        if "last_traded_price" not in out:
            return None
        return out

    def _handle_tick(self, message):
        if not isinstance(message, dict) or message.get("t") not in _TICK_MESSAGE_TYPES:
            return  # not a touchline/depth tick (e.g. an 'ok' subscribe ack)
        try:
            tick = self._merge_and_normalize(message)
        except Exception as e:
            logger.error(f"[shoonya_ws] failed to normalize tick: {e}")
            return
        if tick is None:
            return
        if self._on_tick_cb:
            try:
                self._on_tick_cb(tick)
            except Exception as e:
                logger.error(f"[shoonya_ws] on_tick callback error: {e}")

    def _handle_error(self, error):
        if self._closing:
            return
        logger.error(f"[shoonya_ws] Error: {error}")
        if self._on_error_cb:
            self._on_error_cb(error)

    def _handle_close(self, *_args):
        logger.warning("[shoonya_ws] Connection closed")
        self._connected.clear()
        if self._closing:
            return
        if self._on_close_cb:
            self._on_close_cb()

    def subscribe(self, instruments):
        """instruments: list of 'EXCH|TOKEN' strings, e.g. ['NSE|26000']."""
        instruments = list(instruments)
        with self._desired_lock:
            self._desired.update(instruments)
        if self._connected.is_set():
            self._do_subscribe(instruments)
        # else: _handle_open() replays self._desired once (re)connect completes.

    def _do_subscribe(self, instruments):
        _session.api.subscribe(list(instruments))
        logger.info(f"[shoonya_ws] Subscribed to {len(instruments)} instruments")

    def unsubscribe(self, instruments):
        instruments = list(instruments)
        with self._desired_lock:
            self._desired.difference_update(instruments)
        if self._connected.is_set():
            try:
                _session.api.unsubscribe(list(instruments))
            except Exception as e:
                logger.error(f"[shoonya_ws] unsubscribe failed: {e}")
        with self._state_lock:
            for inst in instruments:
                self._last_state.pop(inst, None)

    def run_forever(self):
        """Blocks the calling thread for a single connection lifetime,
        parking until close() or an unexpected disconnect. connect()
        itself is non-blocking (see its docstring) — this matches
        UpstoxTickStream.run_forever()'s shape rather than
        SmartTickStream's (whose connect() blocks inside ws.connect())."""
        if not self._connected.is_set() and not self._closing:
            self.connect()
        while not self._closing:
            if self._connected.wait(timeout=1):
                break
        while self._connected.is_set() and not self._closing:
            time.sleep(0.5)

    def run_forever_with_reconnect(self, initial_backoff=3, max_backoff=60):
        """Outer retry loop — same shape as SmartTickStream's. Doesn't
        assume the underlying NorenApi websocket has its own auto-
        reconnect (undocumented either way in ShoonyaApi-Py's own docs),
        so this supplies one explicitly rather than assuming it exists."""
        backoff = initial_backoff
        if not self._connected.is_set():
            try:
                self.connect()
            except Exception as e:
                logger.error(f"[shoonya_ws] connect() raised: {e}")

        while not self._closing:
            if not self._connected.wait(timeout=1):
                continue
            # Connected — wait here until it drops or we're told to close.
            while self._connected.is_set() and not self._closing:
                time.sleep(0.5)
            if self._closing:
                break
            backoff_wait = backoff
            logger.warning(f"[shoonya_ws] Disconnected, reconnecting in {backoff_wait}s...")
            time.sleep(backoff_wait)
            backoff = min(backoff * 2, max_backoff)
            try:
                self.connect()
            except Exception as e:
                logger.error(f"[shoonya_ws] Reconnect failed: {e}")
                continue
            else:
                backoff = initial_backoff

        logger.info("[shoonya_ws] Stream loop exited (intentional close)")

    def close(self):
        self._closing = True
        close_fn = getattr(_session.api, "close_websocket", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception as e:
                logger.error(f"[shoonya_ws] close_websocket() raised: {e}")
        self._connected.clear()


# ── __main__ smoke-test ─────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    tick_count = {"n": 0}

    def on_tick(tick):
        tick_count["n"] += 1
        if tick_count["n"] <= 10:
            print(tick)

    stream = ShoonyaTickStream(on_tick=on_tick)
    stream.connect()

    t = threading.Thread(target=stream.run_forever, daemon=True)
    t.start()

    time.sleep(2)
    # Verify this token against your own account/searchscrip before relying
    # on it — index tokens can differ by segment/account configuration.
    stream.subscribe(["NSE|26000"])

    print("Streaming for 15 seconds...")
    time.sleep(15)
    print(f"Total ticks received: {tick_count['n']}")
    stream.close()
