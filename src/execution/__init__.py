"""Broker-neutral execution domain boundaries."""

from .adapters import order_from_paper_record, position_from_paper_record

__all__ = ["order_from_paper_record", "position_from_paper_record"]
