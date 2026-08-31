"""Kotak Neo instrument resolution and WebSocket feed lifecycle.

Mirrors server/feeds/upstox.py: resolves the option-chain token set from
the Kotak scrip master (brokers.kotak.market_data.get_atm_chain), then
drives a KotakTickStream (brokers.kotak.websocket) over those tokens.
Kotak has no whole-expiry Option Chain REST call, so there is no
"fetch the full chain" path here — the chain is reconstructed the same
way the snapshot QuoteProvider does (scrip master + per-token quotes).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from server.feeds.startup import start_resolved_feed


@dataclass
class FeedState:
    """Mutable state for one persistent Kotak Neo market-data connection."""

    stream: Any = None
    aggregator: Any = None
    loop: Any = None
    instruments: list[dict] | None = None
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
    """Build Kotak subscription tokens for one option chain."""
    from brokers.kotak.market_data import get_atm_chain, list_expiries

    symbol = symbol.upper()
    segment = "bse_fo" if is_bse(symbol) else "nse_fo"
    expiries = list_expiries(symbol, exchange=segment)
    if not expiries:
        report(f"[kotak] No expiries found for {symbol}, skipping feed")
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
                f"[kotak] Requested expiry '{expiry}' not available for "
                f"{symbol} (have: {expiries}) — falling back to nearest"
            )
        else:
            resolved_expiry = match

    chain = get_atm_chain(symbol, resolved_expiry, strikes_around_atm, exchange=segment)
    if not chain or not chain.get("rows"):
        report(f"[kotak] Could not build ATM chain for {symbol}, skipping feed")
        return None

    instrument_meta = {
        str(row["token"]): {
            "strike": row["strike"],
            "option_type": row["type"],
            "exchange_segment": segment,
            "is_index": False,
        }
        for row in chain["rows"]
    }
    # Spot/index overlay: Kotak's index websocket names are not yet encoded
    # here (live-verify the exact trading-symbol the feed expects); the spot
    # continues to arrive via the existing NSE/BSE poll until then.
    return instrument_meta, resolved_expiry, None


def _build_subscribe_payload(instrument_meta: dict) -> list[dict]:
    return [
        {
            "instrument_token": token,
            "exchange_segment": meta["exchange_segment"],
            "isIndex": bool(meta.get("is_index", False)),
        }
        for token, meta in instrument_meta.items()
    ]


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
    """Create one Kotak socket and subscribe it to the resolved chain."""
    return start_resolved_feed(
        state, loop, target_symbol, strikes_around_atm, expiry,
        resolve, aggregator_factory, sync_callback, tick_event, stream_factory,
        spawn_thread, wait_for_connection, report,
        build_subscriptions=_build_subscribe_payload,
        stream_kwargs={"mode": "full"},
        format_started=lambda count, symbol, _index, resolved_expiry: (
            f"[kotak] Streaming {count} {symbol} option legs "
            f"(expiry {resolved_expiry})"
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
    """Replace subscriptions while preserving the existing Kotak socket."""
    resolved = resolve(target_symbol, strikes_around_atm, expiry)
    if resolved is None:
        return False
    instrument_meta, resolved_expiry, _index_key = resolved
    new_payload = _build_subscribe_payload(instrument_meta)

    if state.instruments:
        try:
            state.stream.unsubscribe(state.instruments)
        except Exception as exc:  # noqa: BLE001 - SDK failures must not block switching
            report(f"[kotak] Unsubscribe failed (continuing anyway): {exc}")
    state.aggregator.update_token_meta(instrument_meta)
    state.stream.subscribe(new_payload)
    state.instruments = new_payload
    state.current_expiry = resolved_expiry

    report(
        f"[kotak] Switched stream to {len(new_payload)} {target_symbol} option legs "
        f"(expiry {resolved_expiry})"
    )
    return True


def stop_feed(state: FeedState, *, report: Callable[[str], None]) -> bool:
    """Best-effort unsubscribe of the current Kotak instruments."""
    if state.stream is None or not state.instruments:
        return False
    try:
        state.stream.unsubscribe(state.instruments)
    except Exception as exc:  # noqa: BLE001 - SDK cleanup must not block switching
        report(f"[kotak] Unsubscribe failed during shutdown: {exc}")
    finally:
        state.instruments = None
    return True
