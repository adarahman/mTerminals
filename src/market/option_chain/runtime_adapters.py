"""Injected broker capabilities required by option-chain analytics."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BrokerMarketAdapters:
    canonicalize_symbol: Callable[[str], str]
    fetch_chain: Callable[[str, str, str, int], Any]
    list_expiries: Callable[[str, str], list[str]]
    fetch_futures: Callable[[str, str, str], Any]
    warm_batch: Callable[[], Any]
    fetch_ticker_payload: Callable[[], Any]
    fetch_vix: Callable[[], Any]
    fetch_sensex_quote: Callable[[], Any]
