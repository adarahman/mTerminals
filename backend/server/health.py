"""Health snapshot construction and transition logging.

The live server supplies runtime state through :class:`HealthInputs`; this
module deliberately has no broker, HTTP, or WebSocket imports.  That keeps
the health contract independently testable and prevents status polling from
initializing a provider SDK.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable


@dataclass(frozen=True)
class HealthInputs:
    """Read-only state required to build one service-health response."""

    process_started_at: datetime
    market_session_status: Callable[[datetime], str]
    poll_seconds: float
    last_payload: Any
    last_payload_at: datetime | None
    connected_clients: int
    symbol: str
    expiry: str | None
    broker_services_enabled: bool
    data_source: str
    live_feed_provider: str | None
    live_feed_active: bool
    pipeline_status: dict[str, Any]
    smartapi_connected: bool = False
    upstox_connected: bool = False
    shoonya_connected: bool = False


def build_snapshot(inputs: HealthInputs, now: datetime | None = None) -> dict:
    """Build the stable `/health` response from supplied runtime state."""
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    session = inputs.market_session_status(now)
    payload_age = None
    if inputs.last_payload_at is not None:
        payload_age = max(0.0, (now - inputs.last_payload_at).total_seconds())

    stale_after = max(12.0, float(inputs.poll_seconds) * 2.5)
    if (inputs.last_payload is None or inputs.last_payload_at is None) and session == "OPEN":
        feed_status, feed_reason = "STARTING", "No canonical market snapshot has been produced yet"
    elif inputs.last_payload is None or inputs.last_payload_at is None:
        feed_status = "IDLE"
        feed_reason = f"Market session is {session.lower().replace('_', ' ')}; no live snapshot expected"
    elif session == "OPEN" and payload_age > stale_after:
        feed_status, feed_reason = "STALE", f"Canonical market snapshot is {payload_age:.1f}s old"
    elif session == "OPEN":
        feed_status, feed_reason = "LIVE", ""
    else:
        feed_status = "IDLE"
        feed_reason = f"Market session is {session.lower().replace('_', ' ')}"

    degraded = feed_status in {"STARTING", "STALE"}
    reasons = [feed_reason] if degraded and feed_reason else []
    pipeline_delayed = inputs.pipeline_status.get("status") == "DELAYED"
    if pipeline_delayed:
        degraded = True
        reasons.append(inputs.pipeline_status.get("reason") or "Analytics pipeline is delayed")

    return {
        "status": "degraded" if degraded else "ok",
        "service": "mterminals",
        "timestamp": now.isoformat(),
        "uptimeSeconds": max(0.0, (now - inputs.process_started_at).total_seconds()),
        "reasons": reasons,
        "http": {"status": "ok"},
        "websocket": {"status": "ok", "connectedClients": inputs.connected_clients},
        "marketFeed": {
            "status": feed_status,
            "reason": feed_reason,
            "marketSession": session,
            "symbol": inputs.symbol,
            "expiry": inputs.expiry,
            "lastPayloadAt": inputs.last_payload_at.isoformat() if inputs.last_payload_at else None,
            "ageSeconds": round(payload_age, 3) if payload_age is not None else None,
            "staleAfterSeconds": stale_after,
            "smartapiEnabled": inputs.broker_services_enabled,
            "smartapiConnected": inputs.smartapi_connected,
            "dataSource": inputs.data_source,
            "liveFeedProvider": inputs.live_feed_provider if inputs.live_feed_active else None,
            "upstoxConnected": inputs.upstox_connected,
            "shoonyaConnected": inputs.shoonya_connected,
        },
        "analyticsPipeline": dict(inputs.pipeline_status),
    }


def log_transition(snapshot: dict, previous_state, metrics, logger):
    """Log one changed health state and return the state to retain."""
    feed = snapshot.get("marketFeed") or {}
    reasons = tuple(snapshot.get("reasons") or ())
    state = (snapshot.get("status"), feed.get("status"), reasons)
    if state == previous_state:
        return previous_state
    metrics.observe_health_transition(feed.get("status"))
    log = logger.warning if snapshot.get("status") == "degraded" else logger.info
    log(
        "service health transition",
        extra={
            "event": "health.transition",
            "subsystem": "market_feed",
            "status": snapshot.get("status"),
            "reason": "; ".join(reasons) or feed.get("reason") or "",
            "symbol": feed.get("symbol"),
            "expiry": feed.get("expiry"),
            "connected_clients": (snapshot.get("websocket") or {}).get("connectedClients"),
            "age_seconds": feed.get("ageSeconds"),
        },
    )
    return state
