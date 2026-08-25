import asyncio
import json
from datetime import datetime, timedelta, timezone

from server import runtime_state
from application import selection_state


def test_health_is_ok_for_fresh_open_market_snapshot(ws_server_live, monkeypatch):
    module = ws_server_live
    now = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(selection_state, "_market_session_status", lambda _: "OPEN")
    monkeypatch.setattr(runtime_state, "LAST_PAYLOAD", {"symbol": "NIFTY"})
    monkeypatch.setattr(runtime_state, "LAST_PAYLOAD_AT", now - timedelta(seconds=2))

    health = module._build_health_snapshot(now)

    assert health["status"] == "ok"
    assert health["marketFeed"]["status"] == "LIVE"
    assert health["marketFeed"]["ageSeconds"] == 2.0
    assert health["websocket"]["connectedClients"] == len(runtime_state.CONNECTED)


def test_health_degrades_when_open_market_snapshot_is_stale(ws_server_live, monkeypatch):
    module = ws_server_live
    now = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(selection_state, "_market_session_status", lambda _: "OPEN")
    monkeypatch.setattr(runtime_state, "POLL_SECONDS", 5)
    monkeypatch.setattr(runtime_state, "LAST_PAYLOAD", {"symbol": "NIFTY"})
    monkeypatch.setattr(runtime_state, "LAST_PAYLOAD_AT", now - timedelta(seconds=30))

    health = module._build_health_snapshot(now)

    assert health["status"] == "degraded"
    assert health["marketFeed"]["status"] == "STALE"
    assert "30.0s old" in health["reasons"][0]


def test_closed_market_without_snapshot_is_idle_not_failed(ws_server_live, monkeypatch):
    module = ws_server_live
    now = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(selection_state, "_market_session_status", lambda _: "MARKET_CLOSED")
    monkeypatch.setattr(runtime_state, "LAST_PAYLOAD", None)
    monkeypatch.setattr(runtime_state, "LAST_PAYLOAD_AT", None)

    health = module._build_health_snapshot(now)

    assert health["status"] == "ok"
    assert health["marketFeed"]["status"] == "IDLE"
    assert health["reasons"] == []


def test_health_handler_uses_service_status_code(ws_server_live, monkeypatch):
    module = ws_server_live
    monkeypatch.setattr(module, "_build_health_snapshot", lambda: {
        "status": "degraded", "reasons": ["feed stale"]
    })

    response = asyncio.run(module.health_handler(None))

    assert response.status == 503
    assert json.loads(response.text)["reasons"] == ["feed stale"]
