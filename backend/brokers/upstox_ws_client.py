"""
upstox_ws_client.py
====================
Real-time tick streaming via Upstox Market Data Feed V3 — the Upstox
analog of brokers/smartapi_ws_client.py.

"Integration as SmartAPI" means literally that: UpstoxTickStream below
normalizes every incoming tick into the SAME wire schema
smartapi_ws_client.py's SmartTickStream already produces (token,
last_traded_price, open_interest, volume_trade_for_the_day,
closed_price, average_traded_price). That lets it feed straight into
smartapi_feed_adapter.TickAggregator with ZERO changes to that class —
see upstox_feed_adapter.py, which just re-exports TickAggregator as-is
rather than reimplementing per-strike buffering/DOI/volume-delta logic
that has nothing broker-specific about it.

Protobuf decoding is handled internally by the official SDK
(MarketDataStreamerV3) — this module never touches raw protobuf bytes
or a .proto file itself, unlike some community sample code. Requires:

    pip install upstox-python-sdk

That package installs as top-level `upstox_client` — the EXACT same
module name as this codebase's OWN brokers/upstox_client.py. See
_import_official_sdk() below for why that collision is real (not
hypothetical) and how this module avoids it. brokers/upstox_execution_
adapter.py had a latent instance of the same collision in its own
`from upstox_client import ...` (bare, not `from brokers.upstox_client
import ...`) — harmless until now because nothing actually imported the
real PyPI package, but worth fixing alongside this module; see that
file's updated import block.

Usage — mirrors smartapi_ws_client.py's SmartTickStream:

    from brokers.upstox_ws_client import UpstoxTickStream

    def on_tick(tick):
        print(tick)  # {token, last_traded_price, open_interest, ...}

    stream = UpstoxTickStream(on_tick=on_tick, mode="full")
    stream.connect()
    threading.Thread(target=stream.run_forever_with_reconnect, daemon=True).start()
    time.sleep(2)  # let the WS connection establish
    stream.subscribe(["NSE_INDEX|Nifty 50", "NFO_FO|..."])

Modes (Upstox's own vocabulary — see MarketDataStreamerV3's docstring
in the official SDK): 'ltpc' (LTP + prev close only — SmartAPI's
MODE_LTP equivalent), 'full' (LTP + OI + depth + OHLC — SmartAPI's
MODE_SNAP_QUOTE equivalent, and this module's default), 'option_greeks'
(Greeks only), 'full_d30' (full + 30-level depth).

Message shape the SDK delivers (pre-decoded dicts, confirmed against
Upstox's own documented feed shape — 'full' mode, an option/future/
equity instrument):

    {
      "type": "live_feed",
      "feeds": {
        "<instrument_key>": {
          "fullFeed": {
            "marketFF": {                 # equities/options/futures
              "ltpc": {"ltp":.., "ltt":.., "ltq":.., "cp":..},
              "oi": .., "vtt": .., "atp": .., "iv": ..,
              "optionGreeks": {...}, "marketOHLC": {...}, "marketLevel": {...}
            }
            # indices carry "indexFF" instead of "marketFF" — same
            # ltpc shape, but no oi/vtt/marketLevel (indices aren't
            # traded contracts; nothing to have open interest or a
            # bid/ask book on).
          }
        }
      },
      "currentTs": "..."
    }

Unlike SmartAPI's feed, Upstox's ltp/cp/atp are already rupee floats in
both REST and WS (confirmed in upstox_client.py's own module docstring
and this feed's documented example) — no paise-to-rupee division needed
the way smartapi_ws_client.py's _normalize_tick() does.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MODE_LTPC = "ltpc"
MODE_FULL = "full"
MODE_OPTION_GREEKS = "option_greeks"
MODE_FULL_D30 = "full_d30"


def _import_official_sdk():
    """Import the official `upstox-python-sdk` PyPI package (top-level
    module name `upstox_client`) without letting it get shadowed by
    THIS codebase's own brokers/upstox_client.py.

    The collision is real, not theoretical: when this file is run
    standalone from inside brokers/ (see the __main__ smoke-test below —
    same convention smartapi_ws_client.py uses), Python auto-inserts
    brokers/ at sys.path[0], which then shadows the real PyPI package
    with the local file for any bare `import upstox_client` anywhere
    downstream in the process, not just in this module. Importing via
    importlib with this file's own directory excluded from sys.path for
    the duration of the import sidesteps that regardless of how/where
    this module ends up being imported from."""
    import importlib

    this_dir = str(Path(__file__).resolve().parent)
    sys.modules.pop("upstox_client", None)  # drop any prior mis-import
    saved_path = sys.path[:]
    saved_module = sys.modules.get("brokers.upstox_client")
    try:
        sys.path = [p for p in sys.path if p and str(Path(p).resolve()) != this_dir]
        sdk = importlib.import_module("upstox_client")
    except ImportError as exc:
        raise ImportError(
            "upstox_ws_client.py requires the official Upstox Python SDK: "
            "pip install upstox-python-sdk"
        ) from exc
    finally:
        sys.path = saved_path
        # Undo any accidental self-shadowing this import may have caused
        # in sys.modules for OUR OWN module, if something re-imported it
        # under the bare name while brokers/ was briefly off sys.path.
        if saved_module is not None:
            sys.modules["brokers.upstox_client"] = saved_module
    if not hasattr(sdk, "MarketDataStreamerV3"):
        raise ImportError(
            "Imported a module named `upstox_client`, but it has no "
            "MarketDataStreamerV3 — this is almost certainly this "
            "codebase's OWN brokers/upstox_client.py shadowing the real "
            "PyPI package (upstox-python-sdk) rather than the SDK itself. "
            "Check sys.path / how this process was launched."
        )
    return sdk


try:
    from .upstox_client import _session as _own_session
except ImportError:
    from brokers.upstox_client import _session as _own_session


class UpstoxTickStream:
    """Wraps upstox_client.MarketDataStreamerV3 with the same public
    surface SmartTickStream (smartapi_ws_client.py) exposes: connect(),
    subscribe(), unsubscribe(), run_forever(), run_forever_with_reconnect(),
    close(), and a `_connected` threading.Event ws_server_live.py's health
    endpoint already knows how to read (see its `smartapiConnected` check —
    the Upstox equivalent reads this same attribute name off whichever
    stream object is active)."""

    def __init__(self, on_tick=None, on_error=None, on_close=None,
                 mode: str = MODE_FULL, auto_reconnect_interval: int = 5,
                 auto_reconnect_retries: int = 10):
        self.mode = mode
        self._on_tick_cb = on_tick
        self._on_error_cb = on_error
        self._on_close_cb = on_close
        self._auto_reconnect_interval = auto_reconnect_interval
        self._auto_reconnect_retries = auto_reconnect_retries

        self._sdk = None
        self._streamer = None
        self._connected = threading.Event()
        self._closed_event = threading.Event()
        self._closing = False

        # Desired subscription STATE (mirrors SmartTickStream's
        # self._desired) — instrument_key -> mode. Replayed in full on
        # every "open" event so a reconnect (whether the SDK's own
        # auto_reconnect or a fresh connect() call) comes back correctly
        # subscribed without the caller needing to resubscribe manually.
        # Being defensive here even though the SDK's auto_reconnect MAY
        # already preserve subscriptions internally — resubscribing is
        # idempotent, and this codebase's SmartAPI sibling makes the same
        # no-server-side-guarantee assumption.
        self._desired = {}
        self._desired_lock = threading.Lock()

    def connect(self):
        """(Re)builds the underlying MarketDataStreamerV3. Unlike
        SmartTickStream.connect(), this does NOT block the caller — the
        SDK's own connect() spins up its own thread internally (confirmed
        against the SDK's own documented usage: callers sleep/subscribe
        immediately after calling connect(), not after it returns from a
        blocking loop)."""
        if self._sdk is None:
            self._sdk = _import_official_sdk()

        from brokers.upstox_execution_adapter import ensure_session as _ensure_upstox_session
        _ensure_upstox_session()
        token = _own_session.access_token

        configuration = self._sdk.Configuration()
        configuration.access_token = token
        api_client = self._sdk.ApiClient(configuration)

        self._streamer = self._sdk.MarketDataStreamerV3(api_client)
        self._streamer.auto_reconnect(True, self._auto_reconnect_interval, self._auto_reconnect_retries)

        self._streamer.on("open", self._handle_open)
        self._streamer.on("message", self._handle_message)
        self._streamer.on("error", self._handle_error)
        self._streamer.on("close", self._handle_close)
        self._streamer.on("reconnecting", self._handle_reconnecting)
        self._streamer.on("autoReconnectStopped", self._handle_reconnect_stopped)

        self._closing = False
        self._closed_event.clear()
        self._streamer.connect()

    def _handle_open(self, *_args):
        logger.info("[upstox_ws] Connected")
        self._connected.set()
        with self._desired_lock:
            by_mode = {}
            for key, mode in self._desired.items():
                by_mode.setdefault(mode, []).append(key)
        for mode, keys in by_mode.items():
            try:
                self._streamer.subscribe(keys, mode)
                logger.info(f"[upstox_ws] Resubscribed to {len(keys)} keys (mode={mode})")
            except Exception as e:
                logger.error(f"[upstox_ws] resubscribe on open failed: {e}")

    def _handle_reconnecting(self, *args):
        logger.warning(f"[upstox_ws] Reconnecting... {args}")
        self._connected.clear()

    def _handle_reconnect_stopped(self, *args):
        logger.error(f"[upstox_ws] Auto-reconnect exhausted its retry count, giving up: {args}")
        self._connected.clear()
        if self._on_close_cb:
            self._on_close_cb()

    def _normalize_feed(self, instrument_key: str, feed: dict) -> Optional[dict]:
        """One instrument's entry from message['feeds'] -> a tick dict in
        SmartAPI's wire schema (see module docstring). Returns None for a
        feed entry with no usable ltp (shouldn't normally happen, but a
        market-status/heartbeat-only entry could theoretically lack one)."""
        full = feed.get("fullFeed") or {}
        ff = full.get("marketFF") or full.get("indexFF") or {}
        ltpc = ff.get("ltpc") or feed.get("ltpc") or {}
        ltp = ltpc.get("ltp")
        if ltp is None:
            return None

        tick = {
            "token": instrument_key,
            "last_traded_price": ltp,
        }
        cp = ltpc.get("cp")
        if cp is not None:
            tick["closed_price"] = cp

        oi = ff.get("oi")
        if oi is not None:
            try:
                tick["open_interest"] = float(oi)
            except (TypeError, ValueError):
                pass

        vtt = ff.get("vtt")
        if vtt is not None:
            try:
                tick["volume_trade_for_the_day"] = float(vtt)
            except (TypeError, ValueError):
                pass

        atp = ff.get("atp")
        if atp is not None:
            tick["average_traded_price"] = atp

        return tick

    def _handle_message(self, message):
        if not isinstance(message, dict) or message.get("type") != "live_feed":
            return  # market_info / other non-tick message types — ignore
        feeds = message.get("feeds") or {}
        for instrument_key, feed in feeds.items():
            try:
                tick = self._normalize_feed(instrument_key, feed)
            except Exception as e:
                logger.error(f"[upstox_ws] failed to normalize feed for {instrument_key}: {e}")
                continue
            if tick is None:
                continue
            if self._on_tick_cb:
                try:
                    self._on_tick_cb(tick)
                except Exception as e:
                    logger.error(f"[upstox_ws] on_tick callback error: {e}")

    def _handle_error(self, error):
        if self._closing:
            return
        logger.error(f"[upstox_ws] Error: {error}")
        if self._on_error_cb:
            self._on_error_cb(error)

    def _handle_close(self, *args):
        logger.warning(f"[upstox_ws] Connection closed: {args}")
        self._connected.clear()
        self._closed_event.set()
        if self._closing:
            return
        if self._on_close_cb:
            self._on_close_cb()

    def subscribe(self, instrument_keys, mode: str = None):
        mode = mode or self.mode
        with self._desired_lock:
            for key in instrument_keys:
                self._desired[key] = mode
        if self._connected.is_set() and self._streamer is not None:
            self._streamer.subscribe(list(instrument_keys), mode)

    def unsubscribe(self, instrument_keys):
        with self._desired_lock:
            for key in instrument_keys:
                self._desired.pop(key, None)
        if self._connected.is_set() and self._streamer is not None:
            self._streamer.unsubscribe(list(instrument_keys))

    def change_mode(self, instrument_keys, mode: str):
        with self._desired_lock:
            for key in instrument_keys:
                if key in self._desired:
                    self._desired[key] = mode
        if self._connected.is_set() and self._streamer is not None:
            self._streamer.change_mode(list(instrument_keys), mode)

    def run_forever(self):
        """Blocks the calling thread until close() is called. connect()
        itself is non-blocking (see its docstring) — this just parks the
        thread so callers can use the exact same
        `threading.Thread(target=stream.run_forever, daemon=True).start()`
        pattern SmartTickStream uses, even though the underlying SDK
        doesn't require a dedicated blocking thread the way
        SmartWebSocketV2 does."""
        if self._streamer is None:
            self.connect()
        self._closed_event.wait()

    def run_forever_with_reconnect(self, *_args, **_kwargs):
        """Kept for call-site symmetry with SmartTickStream's identically-
        named method (ws_server_live.py's start_upstox_feed() calls this
        exactly like start_smartapi_feed() calls SmartTickStream's
        version). No separate outer backoff loop needed here — the SDK's
        own auto_reconnect(...) (enabled in connect() above) already
        handles reconnection; this just blocks until an intentional
        close() or an exhausted auto-reconnect (_handle_reconnect_stopped)."""
        self.run_forever()

    def close(self):
        self._closing = True
        if self._streamer is not None:
            try:
                self._streamer.disconnect()
            except Exception as e:
                logger.error(f"[upstox_ws] disconnect() raised: {e}")
        self._connected.clear()
        self._closed_event.set()


# ── __main__ smoke-test ─────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    from brokers.upstox_client import INDEX_KEYS

    tick_count = {"n": 0}

    def on_tick(tick):
        tick_count["n"] += 1
        if tick_count["n"] <= 10:
            print(tick)

    stream = UpstoxTickStream(on_tick=on_tick, mode=MODE_FULL)
    stream.connect()

    t = threading.Thread(target=stream.run_forever, daemon=True)
    t.start()

    time.sleep(2)
    stream.subscribe([INDEX_KEYS["NIFTY"], INDEX_KEYS["BANKNIFTY"]])

    print("Streaming for 15 seconds...")
    time.sleep(15)
    print(f"Total ticks received: {tick_count['n']}")
    stream.close()
