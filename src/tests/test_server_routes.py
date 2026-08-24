import asyncio

from server.routes import HttpRouteHandlers


class _HistoryApi:
    async def spot_history(self, request):
        return ("spot", request)

    async def history(self, request):
        return ("history", request)

    async def lot_sizes(self, request):
        return ("lots", request)


def _handlers(calls):
    async def backtest(request, **kwargs):
        calls.append(("backtest", request, kwargs))
        return "backtest-response"

    async def health(request, **kwargs):
        calls.append(("health", request, kwargs))
        return "health-response"

    async def metrics(request, **kwargs):
        calls.append(("metrics", request, kwargs))
        return "metrics-response"

    return HttpRouteHandlers(
        history_api=_HistoryApi(),
        backtest_response=backtest,
        default_symbol=lambda: "NIFTY",
        run_backtest="runner",
        health_response=health,
        health_snapshot="snapshot",
        record_health_transition="transition",
        metrics_response=metrics,
        metrics="metrics-object",
    )


def test_history_routes_delegate_to_history_service():
    handlers = _handlers([])

    assert asyncio.run(handlers.spot_history("request")) == ("spot", "request")
    assert asyncio.run(handlers.history("request")) == ("history", "request")
    assert asyncio.run(handlers.lot_sizes("request")) == ("lots", "request")


def test_operational_routes_receive_injected_dependencies():
    calls = []
    handlers = _handlers(calls)

    assert asyncio.run(handlers.backtest("request")) == "backtest-response"
    assert asyncio.run(handlers.health("request")) == "health-response"
    assert asyncio.run(handlers.metrics("request")) == "metrics-response"

    assert calls == [
        (
            "backtest",
            "request",
            {"default_symbol": "NIFTY", "run_backtest": "runner"},
        ),
        (
            "health",
            "request",
            {"snapshot": "snapshot", "record_transition": "transition"},
        ),
        ("metrics", "request", {"metrics": "metrics-object"}),
    ]
