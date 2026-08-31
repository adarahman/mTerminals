"""Shared startup lifecycle for provider websocket feeds."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def start_resolved_feed(
    state: Any,
    loop: Any,
    target_symbol: str,
    strikes_around_atm: int,
    expiry: str | None,
    resolve: Callable[[str, int, str | None], Any],
    aggregator_factory: Callable[..., Any],
    sync_callback: Callable[..., Any],
    tick_event: Any,
    stream_factory: Callable[..., Any],
    spawn_thread: Callable[..., Any],
    wait_for_connection: Callable[[float], None],
    report: Callable[[str], None],
    *,
    build_subscriptions: Callable[[dict], list],
    format_started: Callable[[int, str, Any, str], str],
    stream_kwargs: dict[str, Any] | None = None,
) -> bool:
    """Resolve, connect, subscribe, and persist one provider feed state."""
    resolved = resolve(target_symbol, strikes_around_atm, expiry)
    if resolved is None:
        return False
    instrument_meta, resolved_expiry, index_instrument = resolved

    state.loop = loop
    state.aggregator = aggregator_factory(
        instrument_meta,
        loop,
        sync_callback,
        tick_event=tick_event,
    )
    state.aggregator.start()
    state.stream = stream_factory(
        on_tick=state.aggregator.on_tick,
        **(stream_kwargs or {}),
    )
    state.stream.connect()
    spawn_thread(target=state.stream.run_forever_with_reconnect, daemon=True).start()
    wait_for_connection(2)

    subscriptions = build_subscriptions(instrument_meta)
    state.stream.subscribe(subscriptions)
    state.instruments = subscriptions
    state.current_expiry = resolved_expiry
    report(
        format_started(
            len(subscriptions), target_symbol, index_instrument, resolved_expiry
        )
    )
    return True
