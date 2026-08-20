"""Canonical broker-neutral WebSocket tick aggregation API.

All provider WebSocket clients normalize into this pipeline's tick schema.
``smartapi_feed_adapter`` remains as the implementation/legacy import name.
"""
from smartapi_feed_adapter import TickAggregator

__all__ = ["TickAggregator"]
