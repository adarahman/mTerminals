"""Regression guard for the shared-executor wiring in _gather_market_data.

The shared process-level I/O executor must be passed to the
ConcurrentMarketDataGatherer *constructor*, not as a keyword to gather()
(which does not accept it). A previous change passed it to gather() and broke
every live poll — this test would have caught that without needing the network.
"""
from types import SimpleNamespace

from application.market_pipeline.input_service import MarketInputService
from application.pipeline_config import RuntimeConfig
from application.market_pipeline.resources import RetirableExecutorPool


class _Sentinel(Exception):
    """Raised by the recorder to stop _gather_market_data before pandas work."""


def test_gather_wiring_passes_executor_to_constructor_only():
    calls: dict = {}

    class RecorderGatherer:
        def __init__(self, **kwargs):
            calls["init"] = dict(kwargs)

        def gather(self, request, timings=None):
            calls["gather"] = {"timings": timings}
            raise _Sentinel()

    cfg = RuntimeConfig(
        symbol="NIFTY",
        expiry="01-Sep-2026",
        no_extra_chains=True,
        use_smartapi=False,
        strict_expiry=False,
        futures_expiry="NEAR",
        operation_timeout_seconds=7.5,
    )
    executor = object()
    service = MarketInputService(
        chain_service=SimpleNamespace(),
        chain_snapshots=SimpleNamespace(),
        executor_pool=SimpleNamespace(get=lambda: executor),
        index_snapshots=SimpleNamespace(get=lambda: None),
        public_market=SimpleNamespace(
            fetch_bse_quote=lambda _symbol: None,
            bse_symbols=(),
        ),
        active_provider=lambda: "NSE_BSE",
        gatherer_factory=RecorderGatherer,
    )
    try:
        service.gather("NSE", cfg, broker_adapters=None, timings={})
    except _Sentinel:
        pass

    assert "init" in calls and "gather" in calls
    # executor must reach the constructor (shared, persistent pool)
    assert "executor" in calls["init"]
    assert calls["init"]["executor"] is executor
    assert calls["init"]["operation_timeout_seconds"] == 7.5
    # and must NOT be forwarded to gather(), which has no such parameter
    assert "executor" not in calls["gather"]
    assert calls["gather"]["timings"] == {}


def test_timed_out_executor_is_retired_without_waiting():
    calls = []

    class Executor:
        def shutdown(self, **kwargs):
            calls.append(kwargs)

    pool = RetirableExecutorPool(
        max_workers=8,
        factory=lambda **_kwargs: Executor(),
        register_shutdown=lambda _shutdown: None,
    )
    first = pool.get()
    pool.retire()
    second = pool.get()

    assert first is not second
    assert calls == [{"wait": False, "cancel_futures": True}]


def test_market_input_service_retires_pool_when_gather_times_out():
    retired = []

    class TimeoutGatherer:
        def __init__(self, **_kwargs):
            pass

        def gather(self, _request, timings=None):
            raise TimeoutError("chain")

    pool = SimpleNamespace(get=lambda: object(), retire=lambda: retired.append(True))
    service = MarketInputService(
        chain_service=SimpleNamespace(),
        chain_snapshots=SimpleNamespace(),
        executor_pool=pool,
        index_snapshots=SimpleNamespace(get=lambda: None),
        public_market=SimpleNamespace(
            fetch_bse_quote=lambda _symbol: None,
            bse_symbols=(),
        ),
        active_provider=lambda: "NSE_BSE",
        gatherer_factory=TimeoutGatherer,
    )

    try:
        service.gather(
            "NSE",
            RuntimeConfig(
                symbol="NIFTY",
                expiry="01-Sep-2026",
                futures_expiry="NEAR",
                use_smartapi=False,
            ),
        )
    except TimeoutError:
        pass

    assert retired == [True]
