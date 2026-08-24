"""
Compatibility layer.

Deprecated:
use application.market_pipeline modules directly.
"""
from __future__ import annotations

import logging
from datetime import datetime

from application.market_pipeline.utils import _canon_underlying

from application.market_pipeline.option_chain import (
    fetch_option_chain_wide,
    get_available_expiries,
)

from application.market_pipeline.futures import (
    fetch_futures_wide,
)

from application.market_pipeline.quotes import (
    fetch_all_pills_and_vix_batched,
    fetch_vix,
    fetch_ticker_payload,
    fetch_sensex_ticker,
)

__all__ = [
    "fetch_option_chain_wide",
    "fetch_futures_wide",
    "fetch_all_pills_and_vix_batched",
    "fetch_vix",
    "fetch_ticker_payload",
    "fetch_sensex_ticker",
    "_canon_underlying",
]