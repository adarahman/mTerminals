"""Regression guard for the shared-executor wiring in _gather_market_data.

The shared process-level I/O executor must be passed to the
ConcurrentMarketDataGatherer *constructor*, not as a keyword to gather()
(which does not accept it). A previous change passed it to gather() and broke
every live poll — this test would have caught that without needing the network.
"""
import pytest
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
    )
    with patch.object(ocr, "ConcurrentMarketDataGatherer", RecorderGatherer):
        try:
            ocr._gather_market_data("NSE", cfg, broker_adapters=None, timings={})
        except _Sentinel:
            pass

    assert "init" in calls and "gather" in calls
    # executor must reach the constructor (shared, persistent pool)
    assert "executor" in calls["init"]
    # and must NOT be forwarded to gather(), which has no such parameter
    assert "executor" not in calls["gather"]
    assert calls["gather"]["timings"] == {}
