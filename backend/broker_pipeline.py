"""Canonical broker-neutral REST pipeline API.

The implementation remains in :mod:`smartapi_pipeline_adapter` temporarily
for backwards compatibility. New code must import this module: it operates
on ``brokers.market_data.market_data`` and therefore supports every active
provider, not just Angel One.
"""
from smartapi_pipeline_adapter import (
    _canon_underlying,
    fetch_all_pills_and_vix_batched,
    fetch_futures_wide,
    fetch_option_chain_wide,
    fetch_sensex_ticker_smartapi,
    fetch_ticker_payload_smartapi,
    fetch_vix_smartapi,
    get_available_expiries,
)

# Broker-neutral names for new call sites. The old suffixed names remain
# available from smartapi_pipeline_adapter.py until the compatibility module
# can be retired.
fetch_vix = fetch_vix_smartapi
fetch_ticker_payload = fetch_ticker_payload_smartapi
fetch_sensex_ticker = fetch_sensex_ticker_smartapi

__all__ = [
    "_canon_underlying",
    "fetch_all_pills_and_vix_batched",
    "fetch_futures_wide",
    "fetch_option_chain_wide",
    "fetch_sensex_ticker",
    "fetch_ticker_payload",
    "fetch_vix",
    "get_available_expiries",
]
