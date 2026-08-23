"""SmartAPI option-feed resolution and persistent socket lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class FeedState:
    stream: Any = None
    aggregator: Any = None
    loop: Any = None
    exchange: str | None = None
    tokens: list[str] | None = None
    current_expiry: str | None = None
    index_token: str | None = None
    index_exchange: str | None = None
    futures_token: str | None = None
    futures_exchange: str | None = None


def resolve_chain_tokens(
    symbol: str,
    strikes: int,
    expiry: str | None,
    *,
    market_data: Any,
    is_bse: Callable[[str], bool],
    parse_expiry: Callable[[str], Any],
    resolve_futures: Callable[[str, str], tuple[Any, Any]],
    report: Callable[[str], None],
):
    """Resolve option, index, and optional futures tokens for a subscription."""
    symbol = symbol.upper()
    exchange = "BFO" if is_bse(symbol) else "NFO"
    expiries = market_data.list_expiries(symbol, exchange=exchange)
    if not expiries:
        report(f"[smartapi] No expiries found for {symbol}, skipping feed")
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
                f"[smartapi] Requested expiry '{expiry}' not available for "
                f"{symbol} (have: {expiries}) — falling back to nearest"
            )
        else:
            resolved_expiry = match

    chain = market_data.get_atm_chain(
        symbol, resolved_expiry, strikes, exchange=exchange
    )
    if not chain:
        report(f"[smartapi] Could not build ATM chain for {symbol}, skipping feed")
        return None

    token_meta = {}
    skipped = 0
    for row in chain.get("rows") or []:
        token = row.get("token") or row.get("instrument_key")
        if not token:
            skipped += 1
            continue
        token_meta[str(token)] = {
            "strike": row.get("strike"),
            "option_type": row.get("type"),
        }
    if skipped and not token_meta:
        report(
            f"[smartapi] No broker tokens resolved for {symbol} {resolved_expiry} "
            f"(provider returned {skipped} token-less rows) — live feed disabled, "
            "falling back to REST poll"
        )

    index_info = market_data.index_tokens().get(symbol)
    index_token = index_exchange = None
    if index_info is None:
        report(
            f"[index-quote] No INDEX_TOKENS entry for {symbol} — spot will only "
            "update via the slower REST poll, not the live feed"
        )
    else:
        index_token = index_info["token"]
        index_exchange = index_info["exchange"] + "_CM"
        token_meta[str(index_token)] = {"strike": None, "option_type": "INDEX"}

    futures_token, futures_exchange = resolve_futures(symbol, exchange)
    if futures_token:
        token_meta[str(futures_token)] = {"strike": None, "option_type": "FUT"}

    return (
        exchange,
        token_meta,
        resolved_expiry,
        index_token,
        index_exchange,
        futures_token,
        futures_exchange,
    )


def start_new_feed(
    state: FeedState,
    loop: Any,
    symbol: str,
    strikes: int,
    expiry: str | None,
    *,
    resolve: Callable[..., Any],
    aggregator_factory: Callable[..., Any],
    callback: Callable[..., Any],
    tick_event: Any,
    stream_factory: Callable[..., Any],
    exchange_types: dict,
    spawn_thread: Callable[..., Any],
    wait: Callable[[float], None],
    report: Callable[[str], None],
) -> bool:
    resolved = resolve(symbol, strikes, expiry)
    if resolved is None:
        return False
    (
        exchange,
        token_meta,
        resolved_expiry,
        index_token,
        index_exchange,
        futures_token,
        _futures_exchange,
    ) = resolved

    state.loop = loop
    state.aggregator = aggregator_factory(
        token_meta, loop, callback, tick_event=tick_event
    )
    state.aggregator.start()
    state.stream = stream_factory(on_tick=state.aggregator.on_tick, mode=3)
    state.stream.connect()
    spawn_thread(target=state.stream.run_forever_with_reconnect, daemon=True).start()
    wait(2)

    option_tokens = [
        token
        for token in token_meta
        if token not in (str(index_token), str(futures_token))
    ]
    fo_tokens = option_tokens + ([str(futures_token)] if futures_token else [])
    state.stream.subscribe(exchange_types[exchange], fo_tokens)
    if index_token:
        state.stream.subscribe(exchange_types[index_exchange], [str(index_token)])

    state.exchange = exchange
    state.tokens = fo_tokens
    state.current_expiry = resolved_expiry
    state.index_token = str(index_token) if index_token else None
    state.index_exchange = index_exchange if index_token else None
    state.futures_token = str(futures_token) if futures_token else None
    state.futures_exchange = exchange if futures_token else None
    report(
        f"[smartapi] Streaming {len(option_tokens)} {symbol} option legs"
        f"{' + spot' if index_token else ''}"
        f"{' + futures VWAP' if futures_token else ''} (expiry {resolved_expiry})"
    )
    return True


def switch_existing_feed(
    state: FeedState,
    symbol: str,
    strikes: int,
    expiry: str | None,
    *,
    resolve: Callable[..., Any],
    exchange_types: dict,
    report: Callable[[str], None],
) -> bool:
    resolved = resolve(symbol, strikes, expiry)
    if resolved is None:
        return False
    (
        exchange,
        token_meta,
        resolved_expiry,
        index_token,
        index_exchange,
        futures_token,
        _futures_exchange,
    ) = resolved
    option_tokens = [
        token
        for token in token_meta
        if token not in (str(index_token), str(futures_token))
    ]
    fo_tokens = option_tokens + ([str(futures_token)] if futures_token else [])

    if state.tokens and state.exchange:
        try:
            state.stream.unsubscribe(exchange_types[state.exchange], state.tokens)
        except Exception as exc:  # noqa: BLE001 - SDK failure must not block switch
            report(f"[smartapi] Unsubscribe failed (continuing anyway): {exc}")
    if state.index_token and state.index_exchange:
        try:
            state.stream.unsubscribe(
                exchange_types[state.index_exchange], [state.index_token]
            )
        except Exception as exc:  # noqa: BLE001 - SDK failure must not block switch
            report(f"[smartapi] Index unsubscribe failed (continuing anyway): {exc}")

    state.aggregator.update_token_meta(token_meta)
    state.stream.subscribe(exchange_types[exchange], fo_tokens)
    if index_token:
        state.stream.subscribe(exchange_types[index_exchange], [str(index_token)])

    state.exchange = exchange
    state.tokens = fo_tokens
    state.current_expiry = resolved_expiry
    state.index_token = str(index_token) if index_token else None
    state.index_exchange = index_exchange if index_token else None
    state.futures_token = str(futures_token) if futures_token else None
    state.futures_exchange = exchange if futures_token else None
    report(
        f"[smartapi] Switched stream to {len(option_tokens)} {symbol} option legs"
        f"{' + spot' if index_token else ''}"
        f"{' + futures VWAP' if futures_token else ''} (expiry {resolved_expiry})"
    )
    return True


def stop_feed(
    state: FeedState,
    *,
    exchange_types: dict,
    report: Callable[[str], None],
) -> bool:
    """Best-effort unsubscribe of SmartAPI derivative and index tokens."""
    if state.stream is None:
        return False
    attempted = False
    if state.tokens and state.exchange:
        attempted = True
        try:
            state.stream.unsubscribe(exchange_types[state.exchange], state.tokens)
        except Exception as exc:  # noqa: BLE001 - SDK cleanup must not block switching
            report(f"[smartapi] Derivative unsubscribe failed during shutdown: {exc}")
    if state.index_token and state.index_exchange:
        attempted = True
        try:
            state.stream.unsubscribe(
                exchange_types[state.index_exchange], [state.index_token]
            )
        except Exception as exc:  # noqa: BLE001 - SDK cleanup must not block switching
            report(f"[smartapi] Index unsubscribe failed during shutdown: {exc}")
    state.tokens = None
    return attempted
