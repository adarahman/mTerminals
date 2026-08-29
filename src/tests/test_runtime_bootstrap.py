from datetime import datetime, timezone
from types import SimpleNamespace

from server.runtime_bootstrap import initialize_runtime_state


def test_runtime_bootstrap_initializes_state_and_paper_services():
    state = SimpleNamespace()
    clients = SimpleNamespace(clients={"client"})
    events = []
    locks = []
    paper_engine = object()
    price_book = object()
    started_at = datetime(2026, 8, 29, tzinfo=timezone.utc)

    def event_factory():
        event = object()
        events.append(event)
        return event

    def lock_factory():
        lock = object()
        locks.append(lock)
        return lock

    result = initialize_runtime_state(
        runtime_state=state,
        instrument_key=lambda *_args: "key",
        environment={"runtime_state.ALGO_STATUS_POLL_SECONDS": "12"},
        now=lambda: started_at,
        client_hub_factory=lambda: clients,
        metrics_factory=lambda **kwargs: ("metrics", kwargs),
        paper_engine_factory=lambda: paper_engine,
        price_book_factory=lambda *_args: price_book,
        event_factory=event_factory,
        lock_factory=lock_factory,
    )

    assert state.DASHBOARD_CLIENTS is clients
    assert state.CONNECTED == {"client"}
    assert state.PROCESS_STARTED_AT is started_at
    assert state.METRICS == ("metrics", {"started_at": started_at})
    assert state.PIPELINE_STATUS["status"] == "STARTING"
    assert state.BASELINE_SEQ == 0
    assert state.BASELINE_ID is None
    assert state.LAST_PAYLOAD is None
    assert state.LAST_KNOWN_LEG_PRICES == {}
    assert state.ALGO_STATUS_POLL_SECONDS == 12
    assert state.SYMBOL_SWITCH_EVENT is events[0]
    assert state.TICK_ACTIVITY_EVENT is events[1]
    assert state.MARKET_STREAM_LOCK is locks[0]
    assert result.paper_engine is paper_engine
    assert result.paper_price_book is price_book
    assert str(result.eod_trigger_time) == "15:45:00"


def test_runtime_bootstrap_uses_safe_status_defaults():
    state = SimpleNamespace()
    result = initialize_runtime_state(
        runtime_state=state,
        instrument_key=lambda *_args: "key",
        environment={},
        client_hub_factory=lambda: SimpleNamespace(clients=set()),
        metrics_factory=lambda **_kwargs: object(),
        paper_engine_factory=object,
        price_book_factory=lambda *_args: object(),
        event_factory=object,
        lock_factory=object,
    )

    assert state.ALGO_STATUS_POLL_SECONDS == 5
    assert state.LAST_FUNDS is None
    assert state.LAST_ALGO_STATUS is None
    assert state.LAST_RECONCILIATION_ALERT is None
    assert state.LAST_LIVE_POSITIONS is None
    assert result is not None
