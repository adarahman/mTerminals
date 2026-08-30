"""Application startup, background execution, and shutdown orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import Any


def build_background_jobs(
    *,
    index_quotes,
    bridge,
    algo_status,
    reconcile,
    live_trading_enabled: bool,
):
    """Create the runtime's named coroutine set without starting tasks."""
    jobs = [
        ("index_quote_loop", index_quotes()),
        ("bridge_loop", bridge()),
        ("algo_status_loop", algo_status()),
    ]
    if live_trading_enabled:
        jobs.append(("position_reconcile_loop", reconcile()))
    return jobs


class ApplicationLifecycle:
    def __init__(
        self,
        *,
        validate_startup: Callable[[], Any],
        configure_logging: Callable[[], Any],
        start_http_server: Callable[[], Awaitable[Any]],
        set_main_loop: Callable[[asyncio.AbstractEventLoop], Any],
        start_live_services: Callable[[asyncio.AbstractEventLoop], Any],
        background_jobs: Callable[[], Iterable[tuple[str, Awaitable[Any]]]],
        create_background_task: Callable[[Awaitable[Any], str], Any],
        run_engine: Callable[[], Awaitable[Any]],
        background_tasks: Callable[[], Iterable[asyncio.Task]],
        close_relay: Callable[[], Awaitable[Any]],
        flush_state: Callable[[], Any],
    ):
        self._validate_startup = validate_startup
        self._configure_logging = configure_logging
        self._start_http_server = start_http_server
        self._set_main_loop = set_main_loop
        self._start_live_services = start_live_services
        self._background_jobs = background_jobs
        self._create_background_task = create_background_task
        self._run_engine = run_engine
        self._background_tasks = background_tasks
        self._close_relay = close_relay
        self._flush_state = flush_state

    async def run(self) -> None:
        self._validate_startup()
        self._configure_logging()
        http_runner = await self._start_http_server()
        loop = asyncio.get_running_loop()
        self._set_main_loop(loop)
        self._start_live_services(loop)

        for name, coroutine in self._background_jobs():
            self._create_background_task(coroutine, name)

        try:
            await self._run_engine()
        finally:
            await self._shutdown(http_runner)

    async def _shutdown(self, http_runner) -> None:
        tasks = list(self._background_tasks())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._close_relay()
        try:
            self._flush_state()
        except Exception as exc:
            print(f"[shutdown] Could not flush buffered state: {exc}")
        if http_runner is not None:
            await http_runner.cleanup()
