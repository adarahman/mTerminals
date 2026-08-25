"""HTTP adapters for service health and operational metrics."""

import logging

from aiohttp import web

from server.health import HealthInputs, build_snapshot, log_transition
from server import runtime_state

from brokers.connection import check_connection

_LOGGER = logging.getLogger("mterminals.server")

_BROKER_PROVIDERS = [
    "KOTAK",
    "UPSTOX",
    "KITE",
    "BREEZE",
    "SHOONYA",
    "SMARTAPI",
]


async def broker_health(request):
    """Per-provider connectivity classification for the Dashboard's
    broker-health panel.

    The ACTIVE market-data source gets a real healthcheck (which may trigger a
    session preflight). Every other provider reports its live-feed connection
    state from runtime_state.FEEDS — no login is attempted for a broker the
    runtime isn't actively using, but the panel still shows a status dot for
    each listed broker instead of marking them all 'unknown'.
    """
    active_provider = runtime_state.MARKET_SELECTION.data_source

    providers = {}

    for provider in _BROKER_PROVIDERS:
        if provider == active_provider:
            status = check_connection(provider)
            providers[provider] = status.as_dict()
        else:
            feed = runtime_state.FEEDS.get(provider)
            connected = bool(getattr(feed, "connected", False)) if feed else False
            providers[provider] = {
                "provider": provider,
                "ready": connected,
                "status": "connected" if connected else "idle",
                "error": None,
            }
        providers[provider]["active"] = (provider == active_provider)

    return web.json_response({"providers": providers})


def log_health_transition(snapshot):
    """Log health changes once; repeated health polls remain quiet."""
    runtime_state.LAST_HEALTH_LOG_STATE = log_transition(
        snapshot, runtime_state.LAST_HEALTH_LOG_STATE, runtime_state.METRICS, _LOGGER
    )

def build_health_snapshot(state, market_session_status, now=None):
    """Construct the health contract from one coordinator state snapshot."""
    return build_snapshot(
        HealthInputs(
            process_started_at=state["process_started_at"],
            market_session_status=market_session_status,
            poll_seconds=state["poll_seconds"],
            last_payload=state["last_payload"],
            last_payload_at=state["last_payload_at"],
            connected_clients=state["connected_clients"],
            symbol=state["symbol"], expiry=state["expiry"],
            broker_services_enabled=state["broker_services_enabled"],
            data_source=state["data_source"],
            live_feed_provider=state["live_feed_provider"],
            live_feed_active=state["live_feed_active"],
            pipeline_status=state["pipeline_status"],
            smartapi_connected=state["smartapi_connected"],
            upstox_connected=state["upstox_connected"],
            shoonya_connected=state["shoonya_connected"],
        ), now=now,
    )


async def health_handler(_request, *, snapshot, record_transition):
    payload = snapshot()
    record_transition(payload)
    return web.json_response(payload, status=200 if payload["status"] == "ok" else 503)


async def metrics_handler(_request, *, metrics):
    return web.json_response(metrics.snapshot())
