import asyncio
import json
from types import SimpleNamespace

from server.dashboard_transport import DashboardBroadcaster


class _Clients:
    def __init__(self):
        self.messages = []

    async def broadcast(self, message, *, on_error):
        self.messages.append(message)
        assert callable(on_error)


def _broadcaster():
    state = SimpleNamespace(
        BASELINE_SEQ=0,
        BASELINE_ID=None,
        DASHBOARD_CLIENTS=_Clients(),
    )
    reports = []
    broadcaster = DashboardBroadcaster(
        runtime_state=state,
        encode=json.dumps,
        report=reports.append,
    )
    return broadcaster, state, reports


def test_full_snapshot_establishes_version_for_following_deltas():
    broadcaster, state, _ = _broadcaster()

    async def exercise():
        await broadcaster.broadcast(
            {"type": "full", "payload": {"symbol": "NIFTY", "expiry": "01SEP2026"}}
        )
        await broadcaster.broadcast({"type": "delta", "payload": {"spot": 25000}})

    asyncio.run(exercise())

    assert state.BASELINE_ID == "NIFTY:01SEP2026:1"
    full, delta = map(json.loads, state.DASHBOARD_CLIENTS.messages)
    assert full["version"] == state.BASELINE_ID
    assert delta["baseVersion"] == state.BASELINE_ID


def test_delta_without_baseline_is_dropped():
    broadcaster, state, reports = _broadcaster()

    asyncio.run(broadcaster.broadcast({"type": "delta", "payload": {"spot": 25000}}))

    assert state.DASHBOARD_CLIENTS.messages == []
    assert reports == [
        "[ws] dropping deltas until a full-snapshot baseline is established"
    ]


def test_missing_baseline_warning_is_emitted_only_once_per_gap():
    broadcaster, state, reports = _broadcaster()

    async def exercise():
        await broadcaster.broadcast({"type": "delta", "payload": {"spot": 1}})
        await broadcaster.broadcast({"type": "delta", "payload": {"spot": 2}})

    asyncio.run(exercise())

    assert state.DASHBOARD_CLIENTS.messages == []
    assert len(reports) == 1
