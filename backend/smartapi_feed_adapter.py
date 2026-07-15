"""
smartapi_feed_adapter.py
=========================
Bridges SmartTickStream (smartapi_ws_client.py) into ws_server_live.py's
ACTUAL broadcast() coroutine — verified directly against both files:

  ws_server_live.py:
    - Single aiohttp asyncio app, one event loop (asyncio.run(main())).
    - async def broadcast(message): sends orjson-encoded {type, payload}
      to every ws in CONNECTED via ws.send_str().
    - compute_diff()'s keyed-list format: {"_keyed": True,
      "_key_field": "strike", "changed": [...], "_removed_keys": [...]}

  Dashboard.js:
    - updateDashboard() branches on msg.type === 'delta' -> applyDelta()
      patches _wsState using that same _keyed format, keyed on `strike`.
    - Chain rows are ONE ROW PER STRIKE combining ceLTP/peLTP/ceOI/peOI/etc.

SmartTickStream's underlying websocket-client loop runs in its own OS
thread (it's NOT asyncio-native), so TickAggregator can't just `await
broadcast(...)` directly — that coroutine must run on ws_server_live.py's
event loop. This adapter uses asyncio.run_coroutine_threadsafe() to hop
from the SmartAPI thread back onto that loop safely.
"""

import time
import asyncio
import threading
from logzero import logger


class TickAggregator:
    """
    token_meta:   dict {token: {"strike": int, "option_type": "CE"/"PE"}}
                  — build once from get_atm_chain()'s rows.
    loop:         the asyncio event loop broadcast() runs on — pass the
                  loop captured inside main() (see wiring notes below).
    broadcast_fn: ws_server_live.py's existing `broadcast` coroutine
                  function itself (not called yet — just the reference).
    flush_interval: seconds between merged-row broadcasts.
    """

    def __init__(self, token_meta, loop, broadcast_fn, flush_interval=0.25):
        self.token_meta = token_meta
        self.loop = loop
        self.broadcast_fn = broadcast_fn
        self.flush_interval = flush_interval
        self._buffer = {}          # strike -> partial row, accumulates ce_*/pe_*
        # Pending {"spot":.., "spotChange":.., "spotChgPct":..} from the
        # underlying INDEX token, if it's ticked since the last flush. Kept
        # separate from _buffer since a spot tick isn't a per-strike chain
        # row and has no "strike" to key on.
        self._spot_buffer = None
        # Persistent per-token caches (survive across flushes, unlike
        # _buffer) — needed to compute real ceDOI/peDOI (OI delta) and
        # ceVolChg/peVolChg (volume delta) per tick. Separate from _buffer
        # because a leg that hasn't ticked in a while still needs its
        # last-known OI/volume as the baseline the next time it does.
        self._prev_oi = {}
        self._prev_vol = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def update_token_meta(self, new_token_meta):
        """Swaps in a new strike/token mapping (e.g. after a symbol switch)
        without tearing down the aggregator or its flush thread. Any
        buffered rows for the OLD symbol are dropped, since they'd be
        stale/meaningless once the chain being displayed has changed."""
        with self._lock:
            self.token_meta = new_token_meta
            self._buffer.clear()
            self._spot_buffer = None
            self._prev_oi.clear()
            self._prev_vol.clear()

    def on_tick(self, tick):
        """Pass directly: SmartTickStream(on_tick=aggregator.on_tick).

        Field names here are camelCase (ceOI, ceLTP, ceVol, ceDOI, ...) —
        confirmed as the actual wire schema chain-views.js and
        ws_server_live.py's own row.get("ceLTP") both read from. An earlier
        version of this file used snake_case (ce_oi, ce_ltp, ...) to match
        oi_analysis.py's internal Python schema, but that's a DIFFERENT,
        pre-transform schema that never reaches the client — that change
        was a mistake and broke ceVol/ceOI updates from SmartAPI entirely
        (they landed in fields nobody read). Keep this camelCase and
        aligned with chain-views.js's mapPayloadToRows() if the wire
        schema changes.
        """
        token = str(tick.get("token"))
        meta = self.token_meta.get(token)
        if not meta:
            return

        if meta.get("option_type") == "INDEX":
            # Underlying spot tick, not an option leg — no strike to key on,
            # so this doesn't belong in _buffer. Goes out as top-level
            # spot/spotChange/spotChgPct fields instead, matching the exact
            # shape run_pipeline_once()'s slower NSE/BSE poll already
            # produces, so nothing on the client (_wsState.spot,
            # priceChart.addTick, etc.) needs to change.
            ltp = tick.get("last_traded_price")
            if ltp is None:
                return
            close = tick.get("closed_price")
            row = {"spot": ltp}
            if close not in (None, 0):
                row["spotChange"] = round(ltp - close, 2)
                row["spotChgPct"] = round((ltp - close) / close * 100, 2)
            with self._lock:
                self._spot_buffer = row
            return

        strike = meta["strike"]
        prefix = "ce" if meta["option_type"] == "CE" else "pe"
        new_oi = tick.get("open_interest")
        new_vol = tick.get("volume_trade_for_the_day")
        ltp = tick.get("last_traded_price")

        with self._lock:
            row = self._buffer.setdefault(strike, {"strike": strike})

            if new_oi is not None:
                prev_oi = self._prev_oi.get(token)
                row[f"{prefix}OI"] = new_oi
                if prev_oi is not None:
                    row[f"{prefix}DOI"] = new_oi - prev_oi   # OI delta — matches chain-views.js's row.ceDOI/peDOI
                self._prev_oi[token] = new_oi
            # else: leave ceOI/peOI untouched rather than writing None over
            # a previously-good value — a stray non-OI-carrying packet
            # shouldn't blank out the last known OI.

            if ltp is not None:
                row[f"{prefix}LTP"] = ltp

            if new_vol is not None:
                prev_vol = self._prev_vol.get(token)
                row[f"{prefix}Vol"] = new_vol
                if prev_vol is not None:
                    row[f"{prefix}VolChg"] = new_vol - prev_vol  # matches chain-views.js's row.ceVolChg/peVolChg
                self._prev_vol[token] = new_vol

            close = tick.get("closed_price")
            if close not in (None, 0) and ltp is not None:
                row[f"{prefix}Chg"] = round(ltp - close, 2)

    def start(self):
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()
        logger.info("[TickAggregator] Flush loop started")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _flush_loop(self):
        # Runs on SmartTickStream's OS thread, NOT the asyncio loop.
        while not self._stop.is_set():
            time.sleep(self.flush_interval)
            with self._lock:
                if not self._buffer and not self._spot_buffer:
                    continue
                changed_rows = list(self._buffer.values())
                self._buffer.clear()
                spot_fields = self._spot_buffer
                self._spot_buffer = None

            payload = dict(spot_fields) if spot_fields else {}
            if changed_rows:
                payload["chain"] = {
                    "_keyed": True,
                    "_key_field": "strike",
                    "changed": changed_rows,
                }

            message = {"type": "delta", "payload": payload}

            # Hop from this thread onto ws_server_live.py's event loop to
            # actually run the `broadcast()` coroutine. Fire-and-forget:
            # we don't block this thread waiting for the send to complete.
            try:
                asyncio.run_coroutine_threadsafe(
                    self.broadcast_fn(message), self.loop
                )
            except Exception as e:
                logger.error(f"[TickAggregator] failed to schedule broadcast: {e}")


# ── Exact wiring for ws_server_live.py ──────────────────────────────────
"""
Add near the top of ws_server_live.py, with the other imports:

    import threading
    from smartapi_client import get_atm_chain, list_expiries
    from smartapi_ws_client import SmartTickStream, EXCHANGE_TYPE
    from smartapi_feed_adapter import TickAggregator

Add a startup function (place it near switch_symbol()/run_pipeline_once()):

    _smartapi_stream = None
    _smartapi_aggregator = None

    def start_smartapi_feed(loop, underlying=None, strikes_around_atm=10):
        '''Starts a live SmartAPI tick feed for `underlying` (defaults to
        the module's current SYMBOL global) and merges ticks into the same
        broadcast() pipeline engine_loop() already uses. Independent of
        engine_loop's own NSE/BSE-driven chain — this ADDS a second,
        faster-updating source for the same `chain` state slice.'''
        global _smartapi_stream, _smartapi_aggregator
        target_symbol = (underlying or SYMBOL).upper()
        exchange = "BFO" if target_symbol in _BSE_SYMBOLS else "NFO"

        expiries = list_expiries(target_symbol, exchange=exchange)
        if not expiries:
            print(f"[smartapi] No expiries found for {target_symbol}, skipping feed")
            return
        chain = get_atm_chain(target_symbol, expiries[0], strikes_around_atm, exchange=exchange)
        if not chain:
            print(f"[smartapi] Could not build ATM chain for {target_symbol}, skipping feed")
            return

        token_meta = {
            row["token"]: {"strike": row["strike"], "option_type": row["type"]}
            for row in chain["rows"]
        }

        _smartapi_aggregator = TickAggregator(token_meta, loop, broadcast)
        _smartapi_aggregator.start()

        _smartapi_stream = SmartTickStream(on_tick=_smartapi_aggregator.on_tick, mode=3)
        _smartapi_stream.connect()
        threading.Thread(target=_smartapi_stream.run_forever, daemon=True).start()
        time.sleep(2)  # let the WS connection establish before subscribing
        _smartapi_stream.subscribe(EXCHANGE_TYPE[exchange], list(token_meta.keys()))
        print(f"[smartapi] Streaming {len(token_meta)} {target_symbol} option legs")

Then inside main(), alongside the existing index_quote_loop task:

    async def main():
        app = web.Application(middlewares=[no_cache_middleware])
        ...
        loop = asyncio.get_running_loop()
        start_smartapi_feed(loop)          # <-- add this line

        try:
            asyncio.create_task(index_quote_loop())
            await engine_loop()
        finally:
            ...

No changes needed in Dashboard.js — applyDelta() already patches `chain`
rows keyed by `strike` exactly as compute_diff() itself would produce
them. Ticks pushed by TickAggregator will render through the exact same
_rerenderChainPanels() path as your existing NSE/BSE-driven updates.

Caveat worth deciding on deliberately: engine_loop() ALSO periodically
rebuilds and broadcasts the full chain from your NSE/BSE pipeline. If both
sources are live simultaneously, whichever writes last to `chain[strike]`
"wins" until the next tick from either side. That's fine if SmartAPI is
your primary real-time leg-level feed and NSE/BSE remains the source for
Greeks/OI-derived analytics (GEX, decision_engine, etc.) that aren't in
SmartAPI's tick payload — but worth being explicit that this is a
supplementary feed, not a replacement, unless/until you decide to retire
the NSE/BSE polling path entirely.
"""
