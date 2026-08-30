"""Daily market scheduling and active feed-aggregator discovery."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any


class DailyMarketScheduler:
    """Own per-day OI resets and once-per-trading-day EOD scheduling."""

    def __init__(
        self,
        *,
        option_aggregators: Callable[[], dict],
        reset_futures_session: Callable[[], Any],
        is_trading_day: Callable[[datetime], bool],
        eod_trigger_time,
        schedule_eod_jobs: Callable[[datetime], Any],
    ):
        self._option_aggregators = option_aggregators
        self._reset_futures_session = reset_futures_session
        self._is_trading_day = is_trading_day
        self._eod_trigger_time = eod_trigger_time
        self._schedule_eod_jobs = schedule_eod_jobs
        self._session_date = None
        self._eod_date = None

    def reset_sessions(self, now: datetime) -> None:
        if self._session_date == now.date():
            return
        self._session_date = now.date()
        for tag, aggregator in self._option_aggregators().items():
            aggregator.reset_session()
            print(
                f"[{tag}] Reset OI session baseline for new trading day "
                f"{now.date()}",
                flush=True,
            )
        self._reset_futures_session()
        print(
            f"[futures_oi] Reset futures OI session baseline for new trading "
            f"day {now.date()}",
            flush=True,
        )

    def trigger_eod(self, now: datetime) -> None:
        if not (
            self._is_trading_day(now)
            and now.time() >= self._eod_trigger_time
            and self._eod_date != now.date()
        ):
            return
        self._eod_date = now.date()
        print(f"[eod] triggering EOD fetch for {now.date()}", flush=True)
        self._schedule_eod_jobs(now)


class LiveFeedAggregatorRegistry:
    """Expose active feed aggregators without leaking manager state internals."""

    def __init__(self, *, managers: Callable[[], dict]):
        self._managers = managers

    def active(self) -> dict:
        aggregators = {}
        for provider, manager in self._managers().items():
            aggregator = manager.aggregator
            if aggregator is not None:
                aggregators[provider.lower()] = aggregator
        return aggregators
