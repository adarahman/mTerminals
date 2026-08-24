"""HTTP adapters for service health and operational metrics."""

from aiohttp import web

from server.health import HealthInputs, build_snapshot

from brokers.connection import check_connection

_BROKER_PROVIDERS = [
    "KOTAK",
    "UPSTOX",
    "KITE",
    "BREEZE",
    "SHOONYA",
    "SMARTAPI",
]


async def broker_health(request):
    providers = {}

    for provider in _BROKER_PROVIDERS:
        status = check_connection(provider)

        providers[provider] = status.as_dict()

    return web.json_response(
        {
            "providers": providers
        }
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
