"""
smartapi_ws_client.py
======================
Real-time tick streaming via SmartAPI WebSocket V2 — replaces repeated
LTP/batch-quote polling with a persistent push feed.

Typical use: subscribe to the same tokens get_atm_chain() resolves, and
feed ticks into your existing _rerenderChainPanels() pipeline instead of
re-fetching on a timer.

Usage
-----
    from smartapi_client import _session, get_atm_chain
    from smartapi_ws_client import SmartTickStream

    chain = get_atm_chain("NIFTY", "31JUL2026", strikes_around_atm=10)
    tokens = [row["token"] for row in chain["rows"]]

    def on_tick(tick):
        print(tick)  # {token, ltp, oi, volume, exchange, ...}

    stream = SmartTickStream(on_tick=on_tick)
    stream.connect()
    stream.subscribe(exchange_type=2, tokens=tokens)  # 2 = NFO
    stream.run_forever()   # blocks; run in its own thread/process
"""

import time
import threading
from logzero import logger

from SmartApi.smartWebSocketV2 import SmartWebSocketV2

try:
    # When imported as part of the mTerminals package (e.g. from
    # ws_server_live.py running out of fno-dashboard/ as cwd).
    from .smartapi_client import _session, API_KEY, CLIENT_CODE
except ImportError:
    # When run standalone/directly (python smartapi_ws_client.py from
    # inside mTerminals/) — no package context exists yet.
    from smartapi_client import _session, API_KEY, CLIENT_CODE

# Angel One exchangeType codes for subscribe payloads
EXCHANGE_TYPE = {
    "NSE_CM": 1,
    "NFO": 2,
    "BSE_CM": 3,
    "BFO": 4,
    "MCX_FO": 5,
    "NCX_FO": 7,
    "CDE_FO": 13,
}

# Subscription modes
MODE_LTP = 1
MODE_QUOTE = 2       # LTP + OHLC + volume
MODE_SNAP_QUOTE = 3  # + best 5 depth + OI


class SmartTickStream:
    def __init__(self, on_tick=None, on_error=None, on_close=None, mode=MODE_QUOTE):
        """
        on_tick:  callback(tick_dict) called for every incoming tick
        mode:     MODE_LTP | MODE_QUOTE | MODE_SNAP_QUOTE
        """
        self.mode = mode
        self._on_tick_cb = on_tick
        self._on_error_cb = on_error
        self._on_close_cb = on_close
        self._ws = None
        self._connected = threading.Event()
        self._closing = False
        # Desired subscription STATE (not a queue) — exchange_type -> set of
        # tokens that SHOULD be subscribed right now. subscribe()/unsubscribe()
        # update this; _handle_open() replays it in full on every (re)connect,
        # so a dropped connection comes back with exactly the right tokens
        # without ws_server_live.py needing to resubscribe manually.
        self._desired = {}
        self._desired_lock = threading.Lock()

    def connect(self):
        """(Re)builds the underlying SmartWebSocketV2 using a fresh
        auth/feed token pull each time — matters for reconnects, since a
        long-dead connection may need a genuinely fresh token, not just a
        retried handshake with a stale one."""
        _session.ensure_session()
        auth_token = _session.auth_token
        feed_token = _session.feed_token

        # SDK defaults to max_retry_attempt=1 — one failed retry and it
        # gives up permanently, calling on_close. That's too thin for a
        # long-lived feed, so we give it real room to recover on its own
        # BEFORE our outer run_forever_with_reconnect() loop even needs to
        # kick in: retry_strategy=1 enables exponential backoff between
        # its own internal attempts.
        self._ws = SmartWebSocketV2(
            auth_token, API_KEY, CLIENT_CODE, feed_token,
            max_retry_attempt=5,
            retry_strategy=1,
            retry_delay=5,
            retry_multiplier=2,
            retry_duration=60,
        )

        self._ws.on_open = self._handle_open
        self._ws.on_data = self._handle_data
        self._ws.on_error = self._handle_error
        self._ws.on_close = self._handle_close

    def _handle_open(self, wsapp):
        logger.info("[smartapi_ws] Connected")
        self._connected.set()
        with self._desired_lock:
            snapshot = {k: list(v) for k, v in self._desired.items() if v}
        for exchange_type, tokens in snapshot.items():
            self._do_subscribe(exchange_type, tokens)

    # Fields SmartAPI's WS feed sends as integer paise (REST endpoints already
    # return these as rupee floats, so we normalize here for consistency
    # across smartapi_client.py and smartapi_ws_client.py).
    _PRICE_FIELDS_PAISE = {
        "last_traded_price",
        "average_traded_price",
        "open_price_of_the_day",
        "high_price_of_the_day",
        "low_price_of_the_day",
        "closed_price",
    }

    def _normalize_tick(self, tick):
        if not isinstance(tick, dict):
            return tick
        for field in self._PRICE_FIELDS_PAISE:
            if field in tick and tick[field] is not None:
                tick[field] = tick[field] / 100.0
        return tick

    def _handle_data(self, wsapp, message):
        message = self._normalize_tick(message)
        if self._on_tick_cb:
            try:
                self._on_tick_cb(message)
            except Exception as e:
                logger.error(f"[smartapi_ws] on_tick callback error: {e}")

    def _handle_error(self, wsapp, error):
        if self._closing:
            return  # expected noise from the library's internal reconnect
                    # logic firing after we intentionally closed the socket
        logger.error(f"[smartapi_ws] Error: {error}")
        if self._on_error_cb:
            self._on_error_cb(error)

    def _handle_close(self, wsapp):
        logger.warning("[smartapi_ws] Connection closed")
        self._connected.clear()
        if self._closing:
            return
        if self._on_close_cb:
            self._on_close_cb()

    def subscribe(self, exchange_type, tokens, correlation_id="mterminals"):
        """
        exchange_type: use EXCHANGE_TYPE['NFO'] etc, or the raw int
        tokens:        list of token strings, e.g. ['26009', '26000']
        """
        if isinstance(exchange_type, str):
            exchange_type = EXCHANGE_TYPE[exchange_type]

        with self._desired_lock:
            self._desired.setdefault(exchange_type, set()).update(tokens)

        if self._connected.is_set():
            self._do_subscribe(exchange_type, tokens, correlation_id)
        # else: _handle_open() will pick this up from self._desired once
        # the (re)connect completes — no separate pending queue needed.

    def _do_subscribe(self, exchange_type, tokens, correlation_id="mterminals"):
        token_list = [{"exchangeType": exchange_type, "tokens": tokens}]
        self._ws.subscribe(correlation_id, self.mode, token_list)
        logger.info(f"[smartapi_ws] Subscribed to {len(tokens)} tokens on exchangeType {exchange_type}")

    def unsubscribe(self, exchange_type, tokens, correlation_id="mterminals"):
        if isinstance(exchange_type, str):
            exchange_type = EXCHANGE_TYPE[exchange_type]

        with self._desired_lock:
            if exchange_type in self._desired:
                self._desired[exchange_type].difference_update(tokens)

        if self._connected.is_set() and self._ws:
            token_list = [{"exchangeType": exchange_type, "tokens": tokens}]
            self._ws.unsubscribe(correlation_id, self.mode, token_list)

    def run_forever(self):
        """Blocks the current thread for a SINGLE connection lifetime — if
        the socket drops, this returns and nothing reconnects. Kept for
        the standalone smoke-test below; ws_server_live.py should use
        run_forever_with_reconnect() instead for a long-lived server."""
        self._ws.connect()

    def run_forever_with_reconnect(self, initial_backoff=3, max_backoff=60):
        """Like run_forever(), but on an unexpected disconnect (i.e. NOT
        from close()), waits with exponential backoff and reconnects —
        replaying the full desired-subscription state via _handle_open()
        so the feed resumes exactly where it left off. This is what
        prevents the "max retry attempts reached -> feed dies forever"
        failure mode: the library's OWN internal retry logic gives up
        after its fixed attempt count, so we supply the outer retry loop
        it doesn't have.

        Run this in its own thread, same as run_forever():
            threading.Thread(target=stream.run_forever_with_reconnect,
                              daemon=True).start()
        """
        backoff = initial_backoff
        while not self._closing:
            try:
                self._ws.connect()  # blocks until this connection ends
            except Exception as e:
                logger.error(f"[smartapi_ws] connect() raised: {e}")

            if self._closing:
                break

            logger.warning(
                f"[smartapi_ws] Disconnected unexpectedly, reconnecting in {backoff}s..."
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)  # back off further if it keeps failing

            try:
                self.connect()  # rebuilds self._ws with a fresh session/tokens
            except Exception as e:
                logger.error(f"[smartapi_ws] Reconnect setup failed: {e}")
                continue
            else:
                backoff = initial_backoff  # reset once a rebuild succeeds

        logger.info("[smartapi_ws] Stream loop exited (intentional close)")

    def close(self):
        self._closing = True
        if self._ws:
            self._ws.close_connection()


# ── __main__ smoke-test ─────────────────────────────────────────────────
if __name__ == "__main__":
    from smartapi_client import get_atm_chain, list_expiries

    expiries = list_expiries("NIFTY", exchange="NFO")
    chain = get_atm_chain("NIFTY", expiries[0], strikes_around_atm=3, exchange="NFO")
    tokens = [row["token"] for row in chain["rows"]]
    print(f"Subscribing to {len(tokens)} NIFTY option tokens near ATM {chain['atm_strike']}")

    tick_count = {"n": 0}

    def on_tick(tick):
        tick_count["n"] += 1
        if tick_count["n"] <= 10:
            print(tick)

    stream = SmartTickStream(on_tick=on_tick, mode=MODE_QUOTE)
    stream.connect()

    t = threading.Thread(target=stream.run_forever, daemon=True)
    t.start()

    time.sleep(2)  # let the connection establish
    stream.subscribe(EXCHANGE_TYPE["NFO"], tokens)

    print("Streaming for 15 seconds...")
    time.sleep(15)
    print(f"Total ticks received: {tick_count['n']}")
    stream.close()
