"""Shared expiry gating and broadcast path for broker websocket ticks."""
from __future__ import annotations

import time
from datetime import datetime

from server import feed_manager, runtime_state
from server.feed_expiry import matches_displayed_expiry
from server.live_feed_state import merge_live_feed_update


_BROADCAST = None
_PORTFOLIO_BROADCASTER = None


def configure(*, broadcast, portfolio_broadcaster):
    """Bind app-level callbacks without importing the composition root."""
    global _BROADCAST, _PORTFOLIO_BROADCASTER
    _BROADCAST = broadcast
    _PORTFOLIO_BROADCASTER = portfolio_broadcaster


def parse_expiry(expiry_str):
    """Normalize the expiry formats emitted by supported providers."""
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(expiry_str, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def matches_current_expiry(provider, payload_expiry_str):
    manager = runtime_state.FEEDS.get(provider)
    current_expiry = manager.current_expiry if manager is not None else None
    return matches_displayed_expiry(current_expiry, payload_expiry_str, parse_expiry)


async def sync_and_broadcast(provider, message):
    """Merge and broadcast a normalized tick only for the active provider."""
    if not feed_manager._feed_allowed(provider):
        return
    async with runtime_state.MARKET_STREAM_LOCK:
        await _sync_and_broadcast_locked(
            message, lambda expiry: matches_current_expiry(provider, expiry)
        )


async def _sync_and_broadcast_locked(message, matches_expiry):
    if _BROADCAST is None:
        return
    feed_update_applied = False
    try:
        message, feed_update_applied = merge_live_feed_update(
            message,
            runtime_state.LAST_PAYLOAD,
            runtime_state.LAST_SENT,
            matches_expiry,
            price_source=runtime_state.MARKET_SELECTION.price_source,
        )
    except Exception as exc:
        print(f"[live-feed] state sync failed (broadcasting anyway): {exc}", flush=True)

    if feed_update_applied and runtime_state.LAST_PAYLOAD is not None:
        runtime_state.LAST_PAYLOAD_AT = datetime.now().astimezone()
    await _BROADCAST(message)

    now_ts = time.monotonic()
    elapsed = now_ts - runtime_state.LAST_PORTFOLIO_BROADCAST_TS
    if elapsed < runtime_state.PORTFOLIO_POLL_SECONDS:
        return
    runtime_state.LAST_PORTFOLIO_BROADCAST_TS = now_ts
    try:
        if _PORTFOLIO_BROADCASTER is not None:
            await _PORTFOLIO_BROADCASTER(runtime_state.LAST_PAYLOAD)
    except Exception as exc:
        print(
            f"[paper-trading] fast-path portfolio broadcast failed: {exc}",
            flush=True,
        )
