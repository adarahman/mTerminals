"""Concurrent, provider-neutral option-chain market input gathering."""
from __future__ import annotations

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
        max_workers: int = 5,
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

    def gather(self, request: MarketDataRequestPlan) -> GatheredMarketInputs:
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            chain = executor.submit(self._fetch_chain, request)
            futures = executor.submit(self._fetch_futures, request)
            indices = executor.submit(self._fetch_indices)

            if request.broker_enabled:
                if self._warm_broker_batch is not None:
                    executor.submit(self._warm_broker_batch).result()
                ticker = self._submit_optional(
                    executor, self._fetch_ticker_payload
                )
                vix = self._submit_optional(executor, self._fetch_vix)
                sensex = self._submit_optional(
                    executor, self._fetch_sensex_quote
                )
                public_quotes = ()
            else:
                ticker = vix = sensex = None
                public_quotes = tuple(
                    executor.submit(self._fetch_public_bse_quote, symbol)
                    for symbol in self._public_bse_symbols
                ) if self._fetch_public_bse_quote is not None else ()

            return GatheredMarketInputs(
                chain=chain.result(),
                futures=futures.result(),
                indices=indices.result(),
                ticker_payload=ticker.result() if ticker is not None else None,
                vix=vix.result() if vix is not None else None,
                sensex_quote=sensex.result() if sensex is not None else None,
                public_bse_quotes=tuple(f.result() for f in public_quotes),
            )

    @staticmethod
    def _submit_optional(executor, operation):
        return executor.submit(operation) if operation is not None else None
