"""Concurrent, provider-neutral option-chain market input gathering."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .requests import MarketDataRequestPlan


@dataclass(frozen=True, slots=True)
class GatheredMarketInputs:
    chain: Any
    futures: Any
    indices: Any
    ticker_payload: Any = None
    vix: Any = None
    sensex_quote: Any = None
    public_bse_quotes: tuple[Any, ...] = ()


class ConcurrentMarketDataGatherer:
    """Fan out independent market reads using injected provider adapters."""

    def __init__(
        self,
        *,
        fetch_chain: Callable[[MarketDataRequestPlan], Any],
        fetch_futures: Callable[[MarketDataRequestPlan], Any],
        fetch_indices: Callable[[], Any],
        warm_broker_batch: Callable[[], Any] | None = None,
        fetch_ticker_payload: Callable[[], Any] | None = None,
        fetch_vix: Callable[[], Any] | None = None,
        fetch_sensex_quote: Callable[[], Any] | None = None,
        fetch_public_bse_quote: Callable[[str], Any] | None = None,
        public_bse_symbols: Iterable[str] = (),
        max_workers: int = 7,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._fetch_chain = fetch_chain
        self._fetch_futures = fetch_futures
        self._fetch_indices = fetch_indices
        self._warm_broker_batch = warm_broker_batch
        self._fetch_ticker_payload = fetch_ticker_payload
        self._fetch_vix = fetch_vix
        self._fetch_sensex_quote = fetch_sensex_quote
        self._fetch_public_bse_quote = fetch_public_bse_quote
        self._public_bse_symbols = tuple(public_bse_symbols)
        self._max_workers = max_workers
        # When provided, a single process-level executor is reused across
        # polls (avoids creating/joining a thread pool every tick). When None,
        # gather() falls back to a per-call executor for isolation.
        self._executor = executor

    def gather(
        self,
        request: MarketDataRequestPlan,
        timings: dict | None = None,
    ) -> GatheredMarketInputs:
        executor = self._executor
        own_executor = executor is None
        if own_executor:
            executor = ThreadPoolExecutor(max_workers=self._max_workers)

        def submit(key: str, operation, *args):
            future = executor.submit(operation, *args)
            if timings is not None:
                submitted_at = time.monotonic()

                def _record(_f, key=key, submitted_at=submitted_at):
                    timings[key] = round(time.monotonic() - submitted_at, 4)

                future.add_done_callback(_record)
            return future

        try:
            chain = submit("chain", self._fetch_chain, request)
            futures = submit("futures", self._fetch_futures, request)
            indices = submit("indices", self._fetch_indices)

            if request.broker_enabled:
                # warm_batch populates the shared batch cache (_BATCH_CACHE)
                # that the ticker/VIX/SENSEX consumers all read. Launch it
                # together with the independent chain/futures/indices fetches,
                # but do NOT start the quote consumers until the batch cache is
                # actually filled — otherwise each consumer independently
                # re-hits the broker API (cache-miss storm) instead of reading
                # the single warmed batch.
                warm = (
                    submit("warm", self._warm_broker_batch)
                    if self._warm_broker_batch is not None
                    else None
                )
                public_quotes = ()
            else:
                warm = None
                public_quotes = (
                    tuple(
                        submit(f"publicBse:{s}", self._fetch_public_bse_quote, s)
                        for s in self._public_bse_symbols
                    )
                    if self._fetch_public_bse_quote is not None
                    else ()
                )

            if warm is not None:
                warm.result()

            # Quote consumers depend on the populated batch cache; only fan
            # them out once warm_batch above has refilled it.
            ticker = vix = sensex = None
            if request.broker_enabled:
                ticker = (
                    submit("ticker", self._fetch_ticker_payload)
                    if self._fetch_ticker_payload is not None
                    else None
                )
                vix = (
                    submit("vix", self._fetch_vix)
                    if self._fetch_vix is not None
                    else None
                )
                sensex = (
                    submit("sensex", self._fetch_sensex_quote)
                    if self._fetch_sensex_quote is not None
                    else None
                )

            return GatheredMarketInputs(
                chain=chain.result(),
                futures=futures.result(),
                indices=indices.result(),
                ticker_payload=ticker.result() if ticker is not None else None,
                vix=vix.result() if vix is not None else None,
                sensex_quote=sensex.result() if sensex is not None else None,
                public_bse_quotes=tuple(f.result() for f in public_quotes),
            )
        finally:
            if own_executor:
                executor.shutdown(wait=True)

    @staticmethod
    def _submit_optional(executor, operation):
        return executor.submit(operation) if operation is not None else None
