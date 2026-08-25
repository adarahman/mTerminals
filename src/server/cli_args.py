"""ws_server_live's command-line surface, separated so the (long) help
texts don't bury the coordinator's wiring. parse_args() still runs at
ws_server_live import time — that module is the process entry point."""

import argparse


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--expiry", default=None)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument(
        "--pipeline-timeout-seconds",
        type=float,
        default=8.0,
        help="Maximum time the live engine waits for one REST analytics pass. "
        "The blocking worker finishes safely in the background while live "
        "websocket ticks and the dashboard remain responsive.",
    )
    parser.add_argument(
        "--min-tick-recompute-seconds",
        type=float,
        default=3.0,
        help="Floor on how often live tick activity can wake engine_loop early. "
        "--poll-seconds is the ceiling (fires anyway if no ticks arrive). Ticks "
        "arrive every ~0.25s during market hours; without this floor the heavy "
        "Greeks/OI-velocity/GEX recompute would run MORE often than the old "
        "fixed poll, not less.",
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--http-port", type=int, default=5500, help="HTTP static file server port")
    parser.add_argument("--relay", action="store_true", help="Enable Node relay posting (off by default)")
    parser.add_argument(
        "--extra-chains",
        action="store_true",
        dest="extra_chains",
        help="Enable multi-expiry NEAR/MONTHLY chains (slower; off by default)",
    )
    parser.add_argument(
        "--strict-expiry",
        action="store_true",
        help="Don't auto-resolve to different expiry if requested expiry has no data",
    )
    parser.add_argument(
        "--no-virtual-oi",
        action="store_true",
        help="Disable VirtualOI model inference for faster performance",
    )
    parser.add_argument(
        "--no-delta",
        action="store_true",
        help="Always broadcast full payloads instead of deltas",
    )
    parser.add_argument(
        "--no-index-quotes",
        action="store_true",
        help="Disable the NIFTY/BANKNIFTY/MIDCPNIFTY/SENSEX ticker-strip background fetch",
    )
    parser.add_argument(
        "--strikes-each-side",
        type=int,
        default=None,
        help="Override engine-side strikes around ATM for analytics "
        "(engine's n_strikes_each_side). Defaults to 15 with broker services "
        "enabled (the live feed overlays fast ticks) and 50 in public-only "
        "REST mode (no fast overlay, so the wider window is needed up front). "
        "Pass explicitly to use the same value in both modes.",
    )
    parser.add_argument(
        "--index-quote-seconds",
        type=int,
        default=20,
        help="How often (s) to refresh the OTHER three indices' ticker quotes. "
        "Separate from --poll-seconds: runs a full pipeline per non-active "
        "symbol and shares NSE rate limits with it.",
    )
    parser.add_argument(
        "--funds-poll-seconds",
        type=int,
        default=15,
        help="How often (s) to refresh real account funds/margin (getRMS) once "
        "Live mode is toggled on (see toggle_live_mode in ws_handler). "
        "Independent of --poll-seconds: RMS limits don't need tick-level "
        "freshness and this is a real network round-trip.",
    )
    parser.add_argument(
        "--portfolio-poll-seconds",
        type=float,
        default=0.5,
        help="Minimum interval (s) between paper-trading portfolio/orders "
        "re-broadcasts triggered off the fast live tick stream. Previously "
        "tied to --poll-seconds, so positions' last_price/P&L lagged the "
        "sub-second chain updates. get_portfolio_summary() is a couple of "
        "small SQLite reads; throttled only to avoid flooding clients during "
        "tick bursts. 0 broadcasts on every live tick unthrottled.",
    )
    return parser