import asyncio

from application.lifecycle import ApplicationLifecycle, build_background_jobs


def test_lifecycle_starts_services_and_shuts_down_every_owner():
    events = []
    tasks = []

    class Runner:
        async def cleanup(self):
            events.append("http-cleanup")

    async def background():
        try:
            await asyncio.Event().wait()
        finally:
            events.append("background-stopped")

    def create_task(coroutine, name):
        events.append(("task", name))
        task = asyncio.create_task(coroutine, name=name)
        tasks.append(task)
        return task

    async def scenario():
        async def start_http():
            events.append("http-started")
            return Runner()

        async def run_engine():
            events.append("engine")
            await asyncio.sleep(0)

        async def close_relay():
            events.append("relay-closed")

        lifecycle = ApplicationLifecycle(
            validate_startup=lambda: events.append("validated"),
            configure_logging=lambda: events.append("logging"),
            start_http_server=start_http,
            set_main_loop=lambda _loop: events.append("loop"),
            start_live_services=lambda _loop: events.append("live-services"),
            background_jobs=lambda: [("worker", background())],
            create_background_task=create_task,
            run_engine=run_engine,
            background_tasks=lambda: tasks,
            close_relay=close_relay,
            flush_state=lambda: events.append("flushed"),
        )
        await lifecycle.run()

    asyncio.run(scenario())

    assert events == [
        "validated",
        "logging",
        "http-started",
        "loop",
        "live-services",
        ("task", "worker"),
        "engine",
        "background-stopped",
        "relay-closed",
        "flushed",
        "http-cleanup",
    ]


def test_flush_failure_does_not_skip_http_cleanup():
    events = []

    class Runner:
        async def cleanup(self):
            events.append("cleanup")

    async def scenario():
        lifecycle = ApplicationLifecycle(
            validate_startup=lambda: None,
            configure_logging=lambda: None,
            start_http_server=lambda: asyncio.sleep(0, result=Runner()),
            set_main_loop=lambda _loop: None,
            start_live_services=lambda _loop: None,
            background_jobs=lambda: [],
            create_background_task=lambda *_args: None,
            run_engine=lambda: asyncio.sleep(0),
            background_tasks=lambda: [],
            close_relay=lambda: asyncio.sleep(0),
            flush_state=lambda: (_ for _ in ()).throw(RuntimeError("disk")),
        )
        await lifecycle.run()

    asyncio.run(scenario())
    assert events == ["cleanup"]


def test_background_job_composition_respects_live_trading_mode():
    async def worker():
        return None

    jobs = build_background_jobs(
        index_quotes=worker,
        bridge=worker,
        algo_status=worker,
        reconcile=worker,
        live_trading_enabled=True,
    )

    assert [name for name, _coroutine in jobs] == [
        "index_quote_loop",
        "bridge_loop",
        "algo_status_loop",
        "position_reconcile_loop",
    ]
    for _name, coroutine in jobs:
        coroutine.close()
