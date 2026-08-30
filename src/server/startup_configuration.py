"""Process-start market configuration and operator-facing source summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from application import selection_state
from market.expiry.service import (
    BSE_EXPIRY_DEFAULT,
    _nearest_Thursday,
    _nearest_Tuesday,
)

DATA_SOURCE_LABELS = {
    "UPSTOX": "Upstox",
    "SHOONYA": "Shoonya",
    "KITE": "Kite",
    "BREEZE": "Breeze",
    "KOTAK": "Kotak",
    "NSE_BSE": "NSE/BSE",
}
EXECUTION_BROKER_LABELS = {
    "SHOONYA": "Shoonya",
    "UPSTOX": "Upstox",
    "KITE": "Zerodha",
    "BREEZE": "ICICI Direct",
}
INDEX_TICKER_SYMBOLS = [
    "NIFTY",
    "BANKNIFTY",
    "MIDCPNIFTY",
    "SENSEX",
    "INDIA VIX",
]
BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}
VIX_TRADINGSYMBOL = "India VIX"
VIX_TOKEN = "99926017"


def resolve_default_pipeline_expiry(symbol: str) -> str:
    """Resolve the nearest valid exchange-calendar option expiry."""
    symbol = (symbol or "").strip().upper()
    if symbol in BSE_SYMBOLS:
        return BSE_EXPIRY_DEFAULT.get(symbol, _nearest_Thursday)()
    return _nearest_Tuesday()


@dataclass(frozen=True, slots=True)
class StartupConfiguration:
    host: str
    websocket_port: int
    http_port: int
    feed_summary: str
    portfolio_summary: str


def configure_startup(
    *,
    args: Any,
    runtime_state: Any,
    broker_services_enabled: bool,
    live_feed_provider: str,
    activate_provider,
    supports_websocket,
) -> StartupConfiguration:
    """Apply CLI-derived process state and build startup status messages."""
    symbol = args.symbol.strip().upper()
    expiry = (
        args.expiry.strip()
        if args.expiry
        else resolve_default_pipeline_expiry(symbol)
    )
    runtime_state.LIVE_FEED_PROVIDER = live_feed_provider
    runtime_state.POLL_SECONDS = args.poll_seconds
    runtime_state.PIPELINE_TIMEOUT_SECONDS = max(1.0, args.pipeline_timeout_seconds)
    runtime_state.MIN_TICK_RECOMPUTE_SECONDS = args.min_tick_recompute_seconds
    runtime_state.USE_RELAY = args.relay
    runtime_state.USE_DELTA = not args.no_delta
    runtime_state.USE_INDEX_QUOTES = not args.no_index_quotes
    runtime_state.INDEX_QUOTE_SECONDS = args.index_quote_seconds
    runtime_state.FUNDS_POLL_SECONDS = args.funds_poll_seconds
    runtime_state.PORTFOLIO_POLL_SECONDS = args.portfolio_poll_seconds
    runtime_state.USE_SMARTAPI = broker_services_enabled
    runtime_state.STRIKES_EACH_SIDE = (
        args.strikes_each_side
        if args.strikes_each_side is not None
        else (15 if broker_services_enabled else 50)
    )

    data_source = selection_state._resolve_default_data_source()
    if not broker_services_enabled:
        data_source = "NSE_BSE"
    activate_provider(data_source)
    runtime_state.MARKET_SELECTION = selection_state.build_market_selection(
        symbol=symbol,
        expiry=expiry,
        data_source=data_source,
        price_source="AUTO",
        futures_expiry="NEAR",
    )

    if data_source == "NSE_BSE":
        chain_source = "NSE/BSE public REST (polling)"
        overlay_state = "no websocket overlay"
    elif broker_services_enabled:
        chain_source = f"{DATA_SOURCE_LABELS.get(data_source, 'SmartAPI')} REST"
        if data_source == live_feed_provider and supports_websocket(data_source):
            overlay_state = f"{live_feed_provider} websocket overlay ENABLED"
        else:
            overlay_state = "no websocket overlay (REST polling)"
    else:
        chain_source = "NSE/BSE public REST (public-only mode)"
        overlay_state = "websocket overlay DISABLED (public-only mode)"

    feed_summary = (
        f"[feed] chain source: {chain_source}, analytics recompute "
        f"ceiling={args.poll_seconds}s floor={args.min_tick_recompute_seconds}s + "
        f"{overlay_state} | index context via market_api.py (20s-cached)"
    )
    cadence = (
        f"every {live_feed_provider.title()} tick (no throttle)"
        if args.portfolio_poll_seconds <= 0
        else f"throttled to >= {args.portfolio_poll_seconds}s"
    )
    public_suffix = (
        ""
        if broker_services_enabled
        else " (inactive — public-only mode, falls back to --poll-seconds cadence)"
    )
    return StartupConfiguration(
        host=args.host,
        websocket_port=args.port,
        http_port=args.http_port,
        feed_summary=feed_summary,
        portfolio_summary=(
            f"[paper-trading] portfolio fast-path broadcast: {cadence}{public_suffix}"
        ),
    )
