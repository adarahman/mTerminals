from types import SimpleNamespace

import pytest

from server.runtime_services import ServerRuntimeServices


class FeedManager:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.started = []

    def _feed_allowed(self, provider):
        return self.allowed

    def _start_live_feed(self, provider, loop):
        self.started.append((provider, loop))


def _services(*, enabled=True, allowed=True, loopback=True, flushed=None):
    state = SimpleNamespace(
        USE_SMARTAPI=enabled,
        LIVE_FEED_PROVIDER="SMARTAPI",
        MARKET_SELECTION=SimpleNamespace(data_source="SMARTAPI"),
        MAIN_LOOP=None,
    )

    async def worker():
        return None

    services = ServerRuntimeServices(
        host="localhost" if loopback else "0.0.0.0",
        runtime_state=state,
        feed_manager=FeedManager(allowed),
        host_is_loopback=lambda host: host == "localhost",
        index_quotes=worker,
        bridge=worker,
        algo_status=worker,
        reconcile=worker,
        live_trading_enabled=False,
        flush_history=lambda: flushed.append(True) if flushed is not None else None,
    )
    return services, state


def test_rejects_non_loopback_control_channel():
    services, _state = _services(loopback=False)

    with pytest.raises(RuntimeError, match="refusing unsafe non-loopback bind"):
        services.validate_startup()


def test_starts_allowed_live_feed_and_records_loop():
    services, state = _services()
    loop = object()

    services.set_main_loop(loop)
    services.start_live_services(loop)

    assert state.MAIN_LOOP is loop
    assert services.feed_manager.started == [("SMARTAPI", loop)]


def test_reports_disabled_overlay_without_starting(capsys):
    services, _state = _services(allowed=False)

    services.start_live_services(object())

    assert services.feed_manager.started == []
    assert "websocket overlay not started" in capsys.readouterr().out


def test_reports_disabled_broker_services(capsys):
    services, _state = _services(enabled=False)

    services.start_live_services(object())

    assert services.feed_manager.started == []
    assert "authenticated services disabled" in capsys.readouterr().out


def test_builds_jobs_and_flushes_state():
    flushed = []
    services, _state = _services(flushed=flushed)

    jobs = services.background_jobs()
    assert [name for name, _coroutine in jobs] == [
        "index_quote_loop",
        "bridge_loop",
        "algo_status_loop",
    ]
    for _name, coroutine in jobs:
        coroutine.close()

    services.flush_state()
    assert flushed == [True]
