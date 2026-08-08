import asyncio
import json
from datetime import datetime, timedelta, timezone

from operational_metrics import OperationalMetrics


def test_registry_tracks_websocket_pipeline_and_feed_lifecycle():
    started = datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)
    metrics = OperationalMetrics(started_at=started)

    metrics.websocket_connected(1)
    metrics.websocket_disconnected(0)
    metrics.websocket_connected(1, reconnect=True)
    metrics.observe_pipeline(True, 0.125, started + timedelta(seconds=10))
    metrics.observe_pipeline(False, 1.5, started + timedelta(seconds=20))
    metrics.observe_health_transition("LIVE")
    metrics.observe_health_transition("STALE")
    metrics.observe_health_transition("LIVE")

    snapshot = metrics.snapshot(started + timedelta(seconds=30))
    counters = snapshot["counters"]
    gauges = snapshot["gauges"]

    assert counters["websocket_connections_total"] == 2
    assert counters["websocket_reconnects_total"] == 1
    assert counters["websocket_disconnections_total"] == 1
    assert counters["pipeline_runs_total"] == 2
    assert counters["pipeline_failures_total"] == 1
    assert counters["health_transitions_total"] == 3
    assert counters["feed_stale_events_total"] == 1
    assert counters["feed_recoveries_total"] == 1
    assert gauges["connectedClients"] == 1
    assert gauges["pipelineDurationSeconds"] == 1.5
    assert gauges["uptimeSeconds"] == 30.0
    assert gauges["lastPipelineSuccessAt"].endswith("+00:00")
    assert gauges["lastPipelineFailureAt"].endswith("+00:00")


def test_snapshot_contract_excludes_market_account_and_order_payloads():
    snapshot = OperationalMetrics().snapshot()
    encoded = json.dumps(snapshot).lower()

    assert set(snapshot) == {"service", "timestamp", "counters", "gauges"}
    for forbidden in ("chain", "order", "position", "account", "credential", "api_key"):
        assert forbidden not in encoded


def test_metrics_handler_returns_registry_snapshot(ws_server_live, monkeypatch):
    module = ws_server_live
    expected = {
        "service": "mterminals", "timestamp": "2026-08-08T00:00:00+00:00",
        "counters": {}, "gauges": {"connectedClients": 0},
    }
    monkeypatch.setattr(module.METRICS, "snapshot", lambda: expected)

    response = asyncio.run(module.metrics_handler(None))

    assert response.status == 200
    assert json.loads(response.text) == expected
