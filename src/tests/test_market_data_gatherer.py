from market.option_chain.gatherer import ConcurrentMarketDataGatherer
from market.option_chain.requests import MarketDataRequestPlan
import threading
import time


def _request(broker_enabled):
    return MarketDataRequestPlan(
        symbol="NIFTY",
        option_expiry="25-Aug-2026",
        option_exchange="NSE",
        strict_expiry=False,
        futures_expiry="NEAR",
        broker_enabled=broker_enabled,
    )


def test_gatherer_collects_broker_context_after_batch_warmup():
    calls = []
    gatherer = ConcurrentMarketDataGatherer(
        fetch_chain=lambda request: "chain",
        fetch_futures=lambda request: "futures",
        fetch_indices=lambda: "indices",
        warm_broker_batch=lambda: calls.append("warm"),
        fetch_ticker_payload=lambda: calls.append("ticker") or ["ticker"],
        fetch_vix=lambda: (20.0, 1.5),
        fetch_sensex_quote=lambda: {"Symbol": "SENSEX"},
    )

    result = gatherer.gather(_request(True))

    assert result.chain == "chain"
    assert result.futures == "futures"
    assert result.indices == "indices"
    assert result.ticker_payload == ["ticker"]
    assert result.vix == (20.0, 1.5)
    assert result.sensex_quote == {"Symbol": "SENSEX"}
    assert calls.index("warm") < calls.index("ticker")


def test_gatherer_uses_public_bse_quotes_without_broker_context():
    gatherer = ConcurrentMarketDataGatherer(
        fetch_chain=lambda request: "chain",
        fetch_futures=lambda request: "futures",
        fetch_indices=lambda: "indices",
        fetch_public_bse_quote=lambda symbol: {"Symbol": symbol},
        public_bse_symbols=("SENSEX", "BANKEX"),
    )

    result = gatherer.gather(_request(False))

    assert result.ticker_payload is None
    assert result.public_bse_quotes == (
        {"Symbol": "SENSEX"},
        {"Symbol": "BANKEX"},
    )


def test_gatherer_bounds_a_stalled_operation():
    import pytest

    release = threading.Event()
    gatherer = ConcurrentMarketDataGatherer(
        fetch_chain=lambda request: release.wait(1),
        fetch_futures=lambda request: "futures",
        fetch_indices=lambda: "indices",
        operation_timeout_seconds=0.01,
    )

    try:
        with pytest.raises(TimeoutError, match="chain"):
            gatherer.gather(_request(False))
    finally:
        release.set()


def test_gatherer_does_not_block_on_optional_batch_warmup():
    release = threading.Event()
    calls = []
    gatherer = ConcurrentMarketDataGatherer(
        fetch_chain=lambda request: "chain",
        fetch_futures=lambda request: "futures",
        fetch_indices=lambda: "indices",
        warm_broker_batch=lambda: release.wait(1),
        fetch_ticker_payload=lambda: calls.append("ticker") or ["cached"],
        operation_timeout_seconds=0.2,
        warm_timeout_seconds=0.01,
    )

    started = time.monotonic()
    try:
        result = gatherer.gather(_request(True))
    finally:
        release.set()

    assert time.monotonic() - started < 0.15
    assert result.ticker_payload == ["cached"]
    assert calls == ["ticker"]
