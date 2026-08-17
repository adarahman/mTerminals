"""
upstox_feed_adapter.py
=======================
Bridges UpstoxTickStream (upstox_ws_client.py) into ws_server_live.py's
broadcast() pipeline — the Upstox analog of smartapi_feed_adapter.py.

There is deliberately NO new aggregator class here. UpstoxTickStream
already normalizes every tick into the exact same wire schema
smartapi_ws_client.py's SmartTickStream produces (token,
last_traded_price, open_interest, volume_trade_for_the_day,
closed_price, average_traded_price — see upstox_ws_client.py's module
docstring), so smartapi_feed_adapter.TickAggregator's per-strike
buffering, session-OI-baseline DOI calculation, and volume-delta logic
apply unchanged: none of that logic is actually SmartAPI-specific, it's
generic "token -> {strike, option_type}" tick aggregation. Re-deriving a
second copy of it here would just be the same class with a different
name, and a second place to fix the same bug in later.

    from smartapi_feed_adapter import TickAggregator
    from upstox_ws_client import UpstoxTickStream

    aggregator = TickAggregator(token_meta, loop, broadcast_fn)
    aggregator.start()
    stream = UpstoxTickStream(on_tick=aggregator.on_tick, mode="full")
    stream.connect()
    threading.Thread(target=stream.run_forever_with_reconnect, daemon=True).start()
    time.sleep(2)
    stream.subscribe(list(token_meta.keys()))

token_meta keys are Upstox instrument_key STRINGS (e.g.
'NFO_FO|12345', 'NSE_INDEX|Nifty 50') rather than SmartAPI's numeric
token strings — TickAggregator doesn't care either way, it only ever
does `str(tick.get("token"))` -> dict lookup, and instrument_key is
already a string.

See ws_server_live.py's start_upstox_feed() / _switch_upstox_symbol_
blocking() / restart_upstox_feed() for the exact wiring actually applied
in this codebase (mirrors start_smartapi_feed() and friends one-for-one,
with Upstox's single instrument_key namespace removing the separate
NFO/BFO-vs-cash-exchange subscribe split SmartAPI's EXCHANGE_TYPE needs).
"""

from smartapi_feed_adapter import TickAggregator  # noqa: F401  (re-exported)
