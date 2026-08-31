"""Shoonya instrument resolution for the server's live tick feed."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from server.feeds.startup import start_resolved_feed


@dataclass
class FeedState:
    """Mutable socket state owned by the server's Shoonya feed instance."""

    stream: Any = None
    aggregator: Any = None
    loop: Any = None
    instruments: list[str] | None = None
    current_expiry: str | None = None


def resolve_chain_tokens(
    target_symbol: str,
    strikes_around_atm: int,
    expiry: str | None,
    is_bse_underlying: Callable[[str], bool],
    parse_expiry: Callable[[str], Any],
    report: Callable[[str], None],
):
    """Build Shoonya ``EXCHANGE|TOKEN`` subscriptions for one option chain.

    Returns ``(instrument_meta, resolved_expiry, index_instrument)`` or
    ``None``.  Server state and socket lifecycle intentionally stay outside
    this resolver, making it safe to reuse during start and symbol switches.
    """
    from brokers.shoonya.market_data import get_atm_chain
    from brokers.shoonya.market_data import index_tokens
    from brokers.shoonya.market_data import list_expiries

    target_symbol = target_symbol.upper()
    exchange = "BFO" if is_bse_underlying(target_symbol) else "NFO"
    expiries = list_expiries(target_symbol, exchange=exchange)
    if not expiries:
        report(f"[shoonya] No expiries found for {target_symbol}, skipping feed")
        return None

    if expiry:
        target_date = parse_expiry(expiry)
        resolved_expiry = next(
            (candidate for candidate in expiries if parse_expiry(candidate) == target_date),
            None,
        )
        if resolved_expiry is None:
            report(
                f"[shoonya] Requested expiry '{expiry}' not available for "
                f"{target_symbol} (have: {expiries}) — falling back to nearest"
            )
            resolved_expiry = expiries[0]
    else:
        resolved_expiry = expiries[0]

    chain = get_atm_chain(
        target_symbol, resolved_expiry, strikes_around_atm, exchange=exchange
    )
    if not chain:
        report(f"[shoonya] Could not build ATM chain for {target_symbol}, skipping feed")
        return None

    instrument_meta = {
        f"{exchange}|{row['token']}": {"strike": row["strike"], "option_type": row["type"]}
        for row in chain["rows"]
    }
    index_info = index_tokens().get(target_symbol)
    index_instrument = None
    if index_info is None or not index_info.get("token"):
        report(
            f"[shoonya] No index token resolved for {target_symbol} — "
            "spot will only update via the slower REST poll, not Shoonya"
        )
    else:
        index_instrument = f"{index_info['exchange']}|{index_info['token']}"
        instrument_meta[index_instrument] = {"strike": None, "option_type": "INDEX"}
    return instrument_meta, resolved_expiry, index_instrument


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
    """Create one Shoonya socket and subscribe it to the resolved chain."""
    return start_resolved_feed(
        state, loop, target_symbol, strikes_around_atm, expiry,
        resolve, aggregator_factory, sync_callback, tick_event, stream_factory,
        spawn_thread, wait_for_connection, report,
        build_subscriptions=list,
        format_started=lambda count, symbol, index, resolved_expiry: (
            f"[shoonya] Streaming {count - (1 if index else 0)} {symbol} option legs"
            f"{' + spot' if index else ''} (expiry {resolved_expiry})"
        ),
    )


def switch_existing_feed(
    state: FeedState,
    target_symbol: str,
    strikes_around_atm: int,
    expiry: str | None,
    resolve: Callable[[str, int, str | None], Any],
    report: Callable[[str], None],
) -> bool:
    """Replace subscriptions on an existing Shoonya connection."""
    resolved = resolve(target_symbol, strikes_around_atm, expiry)
    if resolved is None:
        return False
    instrument_meta, resolved_expiry, index_instrument = resolved
    new_instruments = list(instrument_meta)
    if state.instruments:
        try:
            state.stream.unsubscribe(state.instruments)
        except Exception as exc:
            report(f"[shoonya] Unsubscribe failed (continuing anyway): {exc}")
    state.aggregator.update_token_meta(instrument_meta)
    state.stream.subscribe(new_instruments)
    state.instruments = new_instruments
    state.current_expiry = resolved_expiry
    option_count = len(new_instruments) - (1 if index_instrument else 0)
    report(
        f"[shoonya] Switched stream to {option_count} {target_symbol} option legs"
        f"{' + spot' if index_instrument else ''} (expiry {resolved_expiry})"
    )
    return True


def stop_feed(state: FeedState, *, report: Callable[[str], None]) -> bool:
    """Best-effort unsubscribe of the current Shoonya instruments."""
    if state.stream is None or not state.instruments:
        return False
    try:
        state.stream.unsubscribe(state.instruments)
    except Exception as exc:  # noqa: BLE001 - SDK cleanup must not block switching
        report(f"[shoonya] Unsubscribe failed during shutdown: {exc}")
    finally:
        state.instruments = None
    return True
