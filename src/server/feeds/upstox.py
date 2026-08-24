"""Upstox instrument resolution and WebSocket feed lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class FeedState:
    """Mutable state for one persistent Upstox market-data connection."""

    stream: Any = None
    aggregator: Any = None
    loop: Any = None
    instruments: list[str] | None = None
    current_expiry: str | None = None


def resolve_chain_tokens(
    symbol: str,
    strikes_around_atm: int,
    expiry: str | None,
    *,
    is_bse: Callable[[str], bool],
    parse_expiry: Callable[[str], Any],
    report: Callable[[str], None],
):
    """Build Upstox instrument-key subscriptions for one option chain."""
    from brokers.upstox.client import INDEX_KEYS, get_atm_chain, list_expiries

    symbol = symbol.upper()
    exchange = "BFO" if is_bse(symbol) else "NFO"
    expiries = list_expiries(symbol, exchange=exchange)
    if not expiries:
        report(f"[upstox] No expiries found for {symbol}, skipping feed")
        return None

    resolved_expiry = expiries[0]
    if expiry:
        wanted = parse_expiry(expiry)
        match = next(
            (candidate for candidate in expiries if parse_expiry(candidate) == wanted),
            None,
        )
        if match is None:
            report(
                f"[upstox] Requested expiry '{expiry}' not available for "
                f"{symbol} (have: {expiries}) — falling back to nearest"
            )
        else:
            resolved_expiry = match

    chain = get_atm_chain(
        symbol,
        resolved_expiry,
        strikes_around_atm,
        exchange=exchange,
    )
    if not chain:
        report(f"[upstox] Could not build ATM chain for {symbol}, skipping feed")
        return None

    instrument_meta = {
        row["instrument_key"]: {
            "strike": row["strike"],
            "option_type": row["type"],
        }
        for row in chain["rows"]
        if row.get("instrument_key")
    }
    index_key = INDEX_KEYS.get(symbol)
    if index_key:
        instrument_meta[index_key] = {"strike": None, "option_type": "INDEX"}
    else:
        report(f"[upstox] No INDEX_KEYS entry for {symbol}; spot remains REST-polled")
    return instrument_meta, resolved_expiry, index_key


def start_new_feed(
    state: FeedState,
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
) -> bool:
    """Create one Upstox socket and subscribe it to the resolved chain."""
    resolved = resolve(target_symbol, strikes_around_atm, expiry)
    if resolved is None:
        return False
    instrument_meta, resolved_expiry, index_key = resolved

    state.loop = loop
    state.aggregator = aggregator_factory(
        instrument_meta,
        loop,
        sync_callback,
        tick_event=tick_event,
    )
    state.aggregator.start()
    state.stream = stream_factory(on_tick=state.aggregator.on_tick, mode="full")
    state.stream.connect()
    spawn_thread(target=state.stream.run_forever_with_reconnect, daemon=True).start()
    wait_for_connection(2)

    state.instruments = list(instrument_meta)
    state.stream.subscribe(state.instruments)
    state.current_expiry = resolved_expiry
    option_count = len(state.instruments) - (1 if index_key else 0)
    report(
        f"[upstox] Streaming {option_count} {target_symbol} option legs"
        f"{' + spot' if index_key else ''} (expiry {resolved_expiry})"
    )
    return True


def switch_existing_feed(
    state: FeedState,
    target_symbol: str,
    strikes_around_atm: int,
    expiry: str | None,
    resolve: Callable[[str, int, str | None], Any],
    report: Callable[[str], None],
) -> bool:
    """Replace subscriptions while preserving the existing Upstox socket."""
    resolved = resolve(target_symbol, strikes_around_atm, expiry)
    if resolved is None:
        return False
    instrument_meta, resolved_expiry, index_key = resolved
    new_instruments = list(instrument_meta)

    if state.instruments:
        try:
            state.stream.unsubscribe(state.instruments)
        except Exception as exc:  # noqa: BLE001 - SDK failures must not block switching
            report(f"[upstox] Unsubscribe failed (continuing anyway): {exc}")
    state.aggregator.update_token_meta(instrument_meta)
    state.stream.subscribe(new_instruments)
    state.instruments = new_instruments
    state.current_expiry = resolved_expiry

    option_count = len(new_instruments) - (1 if index_key else 0)
    report(
        f"[upstox] Switched stream to {option_count} {target_symbol} option legs"
        f"{' + spot' if index_key else ''} (expiry {resolved_expiry})"
    )
    return True


def stop_feed(state: FeedState, *, report: Callable[[str], None]) -> bool:
    """Best-effort unsubscribe of the current Upstox instruments."""
    if state.stream is None or not state.instruments:
        return False
    try:
        state.stream.unsubscribe(state.instruments)
    except Exception as exc:  # noqa: BLE001 - SDK cleanup must not block switching
        report(f"[upstox] Unsubscribe failed during shutdown: {exc}")
    finally:
        state.instruments = None
    return True
