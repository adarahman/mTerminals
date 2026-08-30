"""Regression guard for the shared-executor wiring in _gather_market_data.

The shared process-level I/O executor must be passed to the
ConcurrentMarketDataGatherer *constructor*, not as a keyword to gather()
(which does not accept it). A previous change passed it to gather() and broke
every live poll — this test would have caught that without needing the network.
"""
from unittest.mock import patch

from application.pipeline_config import RuntimeConfig
import application.option_chain_runtime as ocr


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
    with patch.object(ocr, "ConcurrentMarketDataGatherer", RecorderGatherer):
        try:
            ocr._gather_market_data("NSE", cfg, broker_adapters=None, timings={})
        except _Sentinel:
            pass

    assert "init" in calls and "gather" in calls
    # executor must reach the constructor (shared, persistent pool)
    assert "executor" in calls["init"]
    assert calls["init"]["operation_timeout_seconds"] == 7.5
    # and must NOT be forwarded to gather(), which has no such parameter
    assert "executor" not in calls["gather"]
    assert calls["gather"]["timings"] == {}


def test_timed_out_executor_is_retired_without_waiting(monkeypatch):
    calls = []

    class Executor:
        def shutdown(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(ocr, "_MARKET_IO_EXECUTOR", Executor())

    ocr._reset_market_io_executor()

    assert ocr._MARKET_IO_EXECUTOR is None
    assert calls == [{"wait": False, "cancel_futures": True}]
