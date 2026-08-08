"""Small dependency-free operational metrics registry for local monitoring."""

from datetime import datetime, timezone
from threading import Lock


class OperationalMetrics:
    COUNTER_NAMES = (
        "websocket_connections_total",
        "websocket_reconnects_total",
        "websocket_disconnections_total",
        "pipeline_runs_total",
        "pipeline_failures_total",
        "health_transitions_total",
        "feed_stale_events_total",
        "feed_recoveries_total",
    )

    def __init__(self, started_at=None):
        self.started_at = started_at or datetime.now(timezone.utc)
        self._lock = Lock()
        self._counters = {name: 0 for name in self.COUNTER_NAMES}
        self._gauges = {
            "connectedClients": 0,
            "pipelineDurationSeconds": None,
            "lastPipelineSuccessAt": None,
            "lastPipelineFailureAt": None,
        }
        self._last_feed_status = None

    @staticmethod
    def _iso(observed_at=None):
        value = observed_at or datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    def websocket_connected(self, connected_clients, reconnect=False):
        with self._lock:
            self._counters["websocket_connections_total"] += 1
            if reconnect:
                self._counters["websocket_reconnects_total"] += 1
            self._gauges["connectedClients"] = max(0, int(connected_clients))

    def websocket_disconnected(self, connected_clients):
        with self._lock:
            self._counters["websocket_disconnections_total"] += 1
            self._gauges["connectedClients"] = max(0, int(connected_clients))

    def observe_pipeline(self, success, duration_seconds, observed_at=None):
        with self._lock:
            self._counters["pipeline_runs_total"] += 1
            self._gauges["pipelineDurationSeconds"] = round(max(0.0, float(duration_seconds)), 6)
            if success:
                self._gauges["lastPipelineSuccessAt"] = self._iso(observed_at)
            else:
                self._counters["pipeline_failures_total"] += 1
                self._gauges["lastPipelineFailureAt"] = self._iso(observed_at)

    def observe_health_transition(self, feed_status):
        status = str(feed_status or "UNKNOWN").upper()
        with self._lock:
            self._counters["health_transitions_total"] += 1
            if status == "STALE":
                self._counters["feed_stale_events_total"] += 1
            if self._last_feed_status == "STALE" and status == "LIVE":
                self._counters["feed_recoveries_total"] += 1
            self._last_feed_status = status

    def snapshot(self, observed_at=None):
        now = observed_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
        gauges["uptimeSeconds"] = round(max(0.0, (now - self.started_at).total_seconds()), 3)
        return {
            "service": "mterminals",
            "timestamp": now.isoformat(),
            "counters": counters,
            "gauges": gauges,
        }
