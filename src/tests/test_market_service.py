import asyncio
import threading
import time
from datetime import datetime, time as datetime_time

from application.market_service import (
    AnalyticsPipelineRunner,
    CanonicalPayloadPublisher,
    DailyMarketScheduler,
    DataSourceSwitcher,
    LiveFeedAggregatorRegistry,
    MarketEngineCycle,
    MarketPipelineService,
    MarketTickPacer,
    OiBaselineSynchronizer,
    PipelineRuntimeConfigurator,
    SerializedPipelineExecutor,
    SymbolSwitcher,
)


def test_successful_pipeline_publishes_live_status():
    statuses = []

    async def publish(*args):
        statuses.append(args)

    service = MarketPipelineService(
        run_pipeline=lambda: asyncio.sleep(0, result={"symbol": "NIFTY"}),
        publish_status=publish,
        pipeline_status={},
        timeout_seconds=1,
        delayed_reason=lambda timeout: f"delayed {timeout}",
        delayed_overlay=lambda: "polling",
    )

    result = asyncio.run(service.collect(time.monotonic()))

    assert result == {"symbol": "NIFTY"}
    assert statuses == [("LIVE",)]
    assert service.in_flight is False


def test_stale_chain_payload_is_published_with_delayed_status():
    statuses = []
    payload = {
        "symbol": "NIFTY",
        "pipelineTimings": {
            "chainStaleReason": "Option chain refresh timed out; using snapshot"
        },
    }

    async def publish(*args):
        statuses.append(args)

    service = MarketPipelineService(
        run_pipeline=lambda: asyncio.sleep(0, result=payload),
        publish_status=publish,
        pipeline_status={},
        timeout_seconds=1,
        delayed_reason=lambda timeout: f"delayed {timeout}",
        delayed_overlay=lambda: "polling",
    )

    result = asyncio.run(service.collect(time.monotonic()))

    assert result is payload
    assert statuses == [
        ("DELAYED", "Option chain refresh timed out; using snapshot")
    ]


def test_timeout_retains_worker_and_collects_it_without_overlap():
    statuses = []
    runs = {"count": 0}

    async def scenario():
        release = asyncio.Event()

        async def pipeline():
            runs["count"] += 1
            await release.wait()
            return {"complete": True}

        async def publish(*args):
            statuses.append(args)

        service = MarketPipelineService(
            run_pipeline=pipeline,
            publish_status=publish,
            pipeline_status={},
            timeout_seconds=0.01,
            delayed_reason=lambda timeout: f"over {timeout}",
            delayed_overlay=lambda: "websocket remains active",
        )

        first = await service.collect(time.monotonic())
        assert first is None
        assert service.in_flight is True
        release.set()
        second = await service.collect(time.monotonic())
        return service, second

    service, result = asyncio.run(scenario())

    assert result == {"complete": True}
    assert runs["count"] == 1
    assert statuses[0][0] == "DELAYED"
    assert statuses[-1] == ("LIVE",)
    assert service.in_flight is False


def test_pipeline_failure_is_reported_and_clears_worker():
    statuses = []

    async def fail():
        raise RuntimeError("broken")

    async def publish(*args):
        statuses.append(args)

    service = MarketPipelineService(
        run_pipeline=fail,
        publish_status=publish,
        pipeline_status={},
        timeout_seconds=1,
        delayed_reason=lambda _timeout: "delayed",
        delayed_overlay=lambda: "polling",
    )

    assert asyncio.run(service.collect(time.monotonic())) is None
    assert statuses == [("DELAYED", "Analytics pipeline failed: broken")]
    assert service.in_flight is False


def test_canonical_publisher_sends_full_then_delta_and_updates_state():
    state = {"canonical": None, "published_at": None, "previous": None}
    messages = []

    async def broadcast(message):
        messages.append(message)

    publisher = CanonicalPayloadPublisher(
        stream_lock=asyncio.Lock(),
        use_delta=lambda: True,
        previous_payload=lambda: state["previous"],
        store_payload=lambda payload, published_at: state.update(
            canonical=payload, published_at=published_at
        ),
        store_previous_payload=lambda payload: state.__setitem__("previous", payload),
        broadcast=broadcast,
        compute_diff=lambda previous, current: {
            "value": current["value"] - previous["value"]
        },
    )

    async def scenario():
        await publisher.publish({"value": 10})
        await publisher.publish({"value": 13})

    asyncio.run(scenario())

    assert messages == [
        {"type": "full", "payload": {"value": 10}},
        {"type": "delta", "payload": {"value": 3}},
    ]
    assert state["canonical"] == {"value": 13}
    assert state["previous"] == {"value": 13}
    assert state["published_at"] is not None


def test_canonical_publisher_skips_unchanged_delta():
    messages = []
    state = {"previous": {"value": 10}}

    async def broadcast(message):
        messages.append(message)

    publisher = CanonicalPayloadPublisher(
        stream_lock=asyncio.Lock(),
        use_delta=lambda: True,
        previous_payload=lambda: state["previous"],
        store_payload=lambda *_args: None,
        store_previous_payload=lambda payload: state.__setitem__("previous", payload),
        broadcast=broadcast,
        compute_diff=lambda _previous, _current: None,
    )

    asyncio.run(publisher.publish({"value": 10}))

    assert messages == []
    assert state["previous"] == {"value": 10}


def test_tick_pacer_wakes_for_symbol_switch_and_clears_event():
    async def scenario():
        symbol_switch = asyncio.Event()
        tick_activity = asyncio.Event()
        symbol_switch.set()
        pacer = MarketTickPacer(
            poll_seconds=1,
            minimum_recompute_seconds=0,
            symbol_switch_event=symbol_switch,
            tick_activity_event=tick_activity,
        )
        await pacer.wait(time.monotonic(), pipeline_elapsed=0)
        return symbol_switch, tick_activity

    symbol_switch, tick_activity = asyncio.run(scenario())

    assert symbol_switch.is_set() is False
    assert tick_activity.is_set() is False


def test_tick_pacer_wakes_for_tick_activity_and_clears_event():
    async def scenario():
        symbol_switch = asyncio.Event()
        tick_activity = asyncio.Event()
        tick_activity.set()
        pacer = MarketTickPacer(
            poll_seconds=1,
            minimum_recompute_seconds=0,
            symbol_switch_event=symbol_switch,
            tick_activity_event=tick_activity,
        )
        await pacer.wait(time.monotonic(), pipeline_elapsed=0)
        return tick_activity

    tick_activity = asyncio.run(scenario())
    assert tick_activity.is_set() is False


def test_tick_pacer_returns_immediately_after_poll_ceiling():
    async def scenario():
        pacer = MarketTickPacer(
            poll_seconds=0.1,
            minimum_recompute_seconds=0,
            symbol_switch_event=asyncio.Event(),
            tick_activity_event=asyncio.Event(),
        )
        started = time.monotonic()
        await pacer.wait(started - 1, pipeline_elapsed=1)
        return time.monotonic() - started

    assert asyncio.run(scenario()) < 0.05


def test_engine_cycle_sequences_successful_market_tick():
    calls = []
    payload = {"decision": {"action": "BUY"}}

    async def collect(_started_at):
        calls.append("collect")
        return payload

    async def publish(value):
        calls.append(("publish", value))

    async def broadcast_portfolio(prices):
        calls.append(("portfolio", prices))

    async def pace(_started_at, _elapsed):
        calls.append("pace")

    cycle = MarketEngineCycle(
        reset_daily_sessions=lambda _now: calls.append("reset"),
        trigger_eod=lambda _now: calls.append("eod"),
        collect_pipeline=collect,
        observe_pipeline=lambda success, _elapsed: calls.append(("metrics", success)),
        market_session_status=lambda _now: "OPEN",
        schedule_auto_execution=lambda decision: calls.append(("auto", decision)),
        seed_oi_baselines=lambda value: calls.append(("seed", value)),
        publish_payload=publish,
        schedule_node_relay=lambda value: calls.append(("relay", value)),
        connected_count=lambda: 2,
        build_current_prices=lambda value: {"spot": value["marketSession"]},
        check_pending_orders=lambda prices: calls.append(("pending", prices)),
        broadcast_portfolio=broadcast_portfolio,
        pace=pace,
    )

    asyncio.run(cycle.run_once())

    assert payload["marketSession"] == "OPEN"
    assert calls == [
        "reset",
        "eod",
        "collect",
        ("metrics", True),
        ("auto", {"action": "BUY"}),
        ("seed", payload),
        ("publish", payload),
        ("relay", payload),
        ("pending", {"spot": "OPEN"}),
        ("portfolio", {"spot": "OPEN"}),
        "pace",
    ]


def test_engine_cycle_still_paces_when_pipeline_has_no_payload():
    calls = []

    async def collect(_started_at):
        return None

    async def pace(_started_at, _elapsed):
        calls.append("pace")

    cycle = MarketEngineCycle(
        reset_daily_sessions=lambda _now: None,
        trigger_eod=lambda _now: None,
        collect_pipeline=collect,
        observe_pipeline=lambda success, _elapsed: calls.append(("metrics", success)),
        market_session_status=lambda _now: "OPEN",
        schedule_auto_execution=lambda _decision: None,
        seed_oi_baselines=lambda _payload: None,
        publish_payload=lambda _payload: asyncio.sleep(0),
        schedule_node_relay=lambda _payload: None,
        connected_count=lambda: 0,
        build_current_prices=lambda _payload: {},
        check_pending_orders=lambda _prices: None,
        broadcast_portfolio=lambda _prices: asyncio.sleep(0),
        pace=pace,
    )

    asyncio.run(cycle.run_once())

    assert calls == [("metrics", False), "pace"]


def test_daily_scheduler_resets_each_session_date_once():
    calls = []

    class Aggregator:
        def reset_session(self):
            calls.append("option-reset")

    scheduler = DailyMarketScheduler(
        option_aggregators=lambda: {"smartapi": Aggregator()},
        reset_futures_session=lambda: calls.append("futures-reset"),
        is_trading_day=lambda _now: True,
        eod_trigger_time=datetime_time(15, 45),
        schedule_eod_jobs=lambda _now: None,
    )

    scheduler.reset_sessions(datetime(2026, 8, 24, 9, 0))
    scheduler.reset_sessions(datetime(2026, 8, 24, 12, 0))
    scheduler.reset_sessions(datetime(2026, 8, 25, 9, 0))

    assert calls == [
        "option-reset",
        "futures-reset",
        "option-reset",
        "futures-reset",
    ]


def test_daily_scheduler_triggers_eod_once_after_cutoff():
    scheduled = []
    scheduler = DailyMarketScheduler(
        option_aggregators=lambda: {},
        reset_futures_session=lambda: None,
        is_trading_day=lambda _now: True,
        eod_trigger_time=datetime_time(15, 45),
        schedule_eod_jobs=lambda now: scheduled.append(now.date()),
    )

    scheduler.trigger_eod(datetime(2026, 8, 24, 15, 44))
    scheduler.trigger_eod(datetime(2026, 8, 24, 15, 45))
    scheduler.trigger_eod(datetime(2026, 8, 24, 16, 0))
    scheduler.trigger_eod(datetime(2026, 8, 25, 15, 45))

    assert [str(value) for value in scheduled] == ["2026-08-24", "2026-08-25"]


def test_oi_synchronizer_maps_exchange_change_to_session_baseline():
    seeded = []

    class Aggregator:
        token_meta = {
            "ce-token": {"strike": 25000, "option_type": "CE"},
            "pe-token": {"strike": 25000, "option_type": "PE"},
            "future-token": {"strike": 25000, "option_type": "FUT"},
        }

        def seed_session_baseline(self, baselines):
            seeded.append(baselines)

    synchronizer = OiBaselineSynchronizer(
        aggregators=lambda: {"smartapi": Aggregator()}
    )
    synchronizer.synchronize(
        {
            "chain": [
                {
                    "strike": 25000,
                    "ceOI": 1200,
                    "ceChgOI": 200,
                    "peOI": 900,
                    "peChgOI": -100,
                }
            ]
        }
    )

    assert seeded == [{"ce-token": 1000, "pe-token": 1000}]


def test_oi_synchronizer_skips_false_zero_and_missing_sides():
    seeded = []

    class Aggregator:
        token_meta = {
            "ce-token": {"strike": 25000, "option_type": "CE"},
            "pe-token": {"strike": 25100, "option_type": "PE"},
        }

        def seed_session_baseline(self, baselines):
            seeded.append(baselines)

    OiBaselineSynchronizer(
        aggregators=lambda: {"upstox": Aggregator()}
    ).synchronize(
        {
            "chain": [
                {"strike": 25000, "ceOI": 0, "ceChgOI": 0},
                {"strike": 25100, "peOI": 500},
            ]
        }
    )

    assert seeded == []


def test_live_feed_registry_returns_only_active_aggregators():
    smartapi = object()

    class Manager:
        def __init__(self, aggregator):
            self.aggregator = aggregator

    registry = LiveFeedAggregatorRegistry(
        managers=lambda: {
            "SMARTAPI": Manager(smartapi),
            "UPSTOX": Manager(None),
            "SHOONYA": Manager(None),
        }
    )

    assert registry.active() == {"smartapi": smartapi}


def test_analytics_runner_configures_captures_and_returns_payload():
    calls = []
    capture = {}

    def invoke(config):
        calls.append(("invoke", config))
        capture["payload"] = {"symbol": "NIFTY"}

    runner = AnalyticsPipelineRunner(
        configure=lambda: calls.append("configure") or "runtime-config",
        clear_capture=lambda: calls.append("clear") or capture.clear(),
        invoke=invoke,
        captured_payload=lambda: capture.get("payload"),
    )

    assert runner.run_once() == {"symbol": "NIFTY"}
    assert calls == [
        "configure",
        "clear",
        ("invoke", "runtime-config"),
    ]


def test_analytics_runner_contains_legacy_failure():
    runner = AnalyticsPipelineRunner(
        configure=lambda: None,
        clear_capture=lambda: None,
        invoke=lambda config: (_ for _ in ()).throw(
            RuntimeError("legacy failure")
        ),
        captured_payload=lambda: {"must": "not return"},
    )

    assert runner.run_once() is None


def test_pipeline_runtime_configurator_builds_and_applies_default_config():
    activated = []
    applied = []
    configurator = PipelineRuntimeConfigurator(
        data_source=lambda: "SMARTAPI",
        activate_provider=activated.append,
        resolve_default_expiry=lambda symbol: f"default-{symbol}",
        apply_config=applied.append,
    )

    config = configurator.configure(
        symbol="NIFTY",
        strict_expiry=True,
        price_source="fut",
        strikes_each_side=25,
    )

    assert activated == ["SMARTAPI"]
    assert applied == [config]
    assert config.symbol == "NIFTY"
    assert config.expiry == "default-NIFTY"
    assert config.strict_expiry is True
    assert config.price_source == "FUT"
    assert config.strikes_each_side == 25
    assert config.use_smartapi is True


def test_pipeline_runtime_configurator_preserves_explicit_expiry():
    resolved = []
    configurator = PipelineRuntimeConfigurator(
        data_source=lambda: "NSE_BSE",
        activate_provider=lambda source: None,
        resolve_default_expiry=lambda symbol: resolved.append(symbol),
        apply_config=lambda config: None,
    )

    config = configurator.configure(symbol="SENSEX", expiry="2026-08-27")

    assert resolved == []
    assert config.expiry == "2026-08-27"
    assert config.use_smartapi is False


def test_serialized_pipeline_executor_blocks_state_change_during_worker():
    async def scenario():
        executor = SerializedPipelineExecutor()
        started = threading.Event()
        release = threading.Event()
        mutations = []
        event_loop_thread = threading.get_ident()

        def blocking_pipeline():
            started.set()
            release.wait(timeout=2)
            return threading.get_ident()

        pipeline_task = asyncio.create_task(
            executor.run_blocking(blocking_pipeline)
        )
        await asyncio.to_thread(started.wait, 2)
        mutation_task = asyncio.create_task(
            executor.run_exclusive(lambda: mutations.append("switched"))
        )
        await asyncio.sleep(0.01)
        assert mutations == []
        release.set()
        worker_thread = await pipeline_task
        await mutation_task
        return event_loop_thread, worker_thread, mutations

    event_loop_thread, worker_thread, mutations = asyncio.run(scenario())

    assert worker_thread != event_loop_thread
    assert mutations == ["switched"]


def test_serialized_pipeline_executor_releases_lock_after_failure():
    async def scenario():
        executor = SerializedPipelineExecutor()
        try:
            await executor.run_blocking(
                lambda: (_ for _ in ()).throw(RuntimeError("failed"))
            )
        except RuntimeError:
            pass
        return await executor.run_exclusive(lambda: "recovered")

    assert asyncio.run(scenario()) == "recovered"


def test_data_source_switcher_commits_and_restarts_websocket_feed():
    state = {"source": "NSE_BSE"}
    calls = []
    switcher = DataSourceSwitcher(
        valid_sources=lambda: {"NSE_BSE", "UPSTOX"},
        current_source=lambda: state["source"],
        execution_gate=SerializedPipelineExecutor(),
        activate_provider=lambda source: calls.append(("activate", source)) or True,
        stop_feed=lambda source: calls.append(("stop", source)),
        commit_source=lambda source: state.update(source=source),
        supports_websocket=lambda source: source == "UPSTOX",
        restart_feed=lambda source, symbol, expiry: calls.append(
            ("restart", source, symbol, expiry)
        ),
        current_symbol=lambda: "NIFTY",
        current_expiry=lambda: "2026-08-27",
        signal_refresh=lambda: calls.append(("refresh",)),
    )

    result = asyncio.run(switcher.switch(" upstox "))

    assert result is True
    assert state["source"] == "UPSTOX"
    assert calls == [
        ("activate", "UPSTOX"),
        ("stop", "NSE_BSE"),
        ("restart", "UPSTOX", "NIFTY", "2026-08-27"),
        ("refresh",),
    ]


def test_data_source_switcher_does_not_commit_unavailable_provider():
    state = {"source": "NSE_BSE"}
    committed = []
    switcher = DataSourceSwitcher(
        valid_sources=lambda: {"NSE_BSE", "KITE"},
        current_source=lambda: state["source"],
        execution_gate=SerializedPipelineExecutor(),
        activate_provider=lambda source: False,
        stop_feed=lambda source: None,
        commit_source=committed.append,
        supports_websocket=lambda source: True,
        restart_feed=lambda source, symbol, expiry: None,
        current_symbol=lambda: "NIFTY",
        current_expiry=lambda: None,
        signal_refresh=lambda: None,
    )

    assert asyncio.run(switcher.switch("KITE")) is False
    assert committed == []


def test_data_source_switcher_rejects_unknown_provider():
    switcher = DataSourceSwitcher(
        valid_sources=lambda: {"NSE_BSE"},
        current_source=lambda: "NSE_BSE",
        execution_gate=SerializedPipelineExecutor(),
        activate_provider=lambda source: True,
        stop_feed=lambda source: None,
        commit_source=lambda source: None,
        supports_websocket=lambda source: False,
        restart_feed=lambda source, symbol, expiry: None,
        current_symbol=lambda: "NIFTY",
        current_expiry=lambda: None,
        signal_refresh=lambda: None,
    )

    try:
        asyncio.run(switcher.switch("unknown"))
    except ValueError as exc:
        assert "UNKNOWN" in str(exc)
    else:
        raise AssertionError("unknown provider was accepted")


def test_data_source_switch_waits_for_inflight_pipeline():
    """A provider switch must serialize with the analytics pipeline: while a
    pass holds the execution gate, a switch cannot commit a new provider."""
    executor = SerializedPipelineExecutor()
    state = {"source": "NSE_BSE"}
    switcher = DataSourceSwitcher(
        valid_sources=lambda: {"NSE_BSE", "UPSTOX"},
        current_source=lambda: state["source"],
        execution_gate=executor,
        activate_provider=lambda source: True,
        stop_feed=lambda source: None,
        commit_source=lambda source: state.update(source=source),
        supports_websocket=lambda source: False,
        restart_feed=lambda *a: None,
        current_symbol=lambda: "NIFTY",
        current_expiry=lambda: None,
        signal_refresh=lambda: None,
    )

    async def scenario():
        # Mimic the real analytics pass, which holds the gate for the whole run.
        pipeline = asyncio.create_task(
            executor.run_blocking(lambda: time.sleep(0.1))
        )
        await asyncio.sleep(0.02)  # let the pipeline acquire the gate

        switch_task = asyncio.create_task(switcher.switch("UPSTOX"))
        # Give the switch every chance to run if it were NOT gated.
        await asyncio.sleep(0.05)
        assert state["source"] == "NSE_BSE", (
            "switch committed before the in-flight pipeline released the gate"
        )

        await pipeline
        result = await switch_task
        assert result is True
        assert state["source"] == "UPSTOX"

    asyncio.run(scenario())


def test_symbol_switcher_normalizes_commits_and_restarts_live_feed():
    state = {"symbol": "NIFTY", "expiry": "old"}
    calls = []
    switcher = SymbolSwitcher(
        current_symbol=lambda: state["symbol"],
        current_expiry=lambda: state["expiry"],
        commit_selection=lambda symbol, expiry: state.update(
            symbol=symbol, expiry=expiry
        ),
        signal_refresh=lambda: calls.append(("refresh",)),
        live_feed_enabled=lambda: True,
        live_feed_provider=lambda: "UPSTOX",
        restart_feed=lambda provider, symbol, expiry: calls.append(
            ("restart", provider, symbol, expiry)
        ),
    )

    result = switcher.switch(
        "zydus%20lifesciences%20ltd", "2026-08-27"
    )

    assert result is True
    assert state == {
        "symbol": "ZYDUS LIFESCIENCES LTD",
        "expiry": "2026-08-27",
    }
    assert calls == [
        ("refresh",),
        ("restart", "UPSTOX", "ZYDUS LIFESCIENCES LTD", "2026-08-27"),
    ]


def test_symbol_switcher_same_symbol_without_expiry_is_noop():
    committed = []
    switcher = SymbolSwitcher(
        current_symbol=lambda: "NIFTY",
        current_expiry=lambda: "2026-08-27",
        commit_selection=lambda symbol, expiry: committed.append((symbol, expiry)),
        signal_refresh=lambda: None,
        live_feed_enabled=lambda: False,
        live_feed_provider=lambda: "NSE_BSE",
        restart_feed=lambda provider, symbol, expiry: None,
    )

    assert switcher.switch(" nifty ") is None
    assert committed == []


def test_symbol_switcher_expiry_change_commits_without_disabled_feed_restart():
    committed = []
    restarted = []
    switcher = SymbolSwitcher(
        current_symbol=lambda: "NIFTY",
        current_expiry=lambda: "old",
        commit_selection=lambda symbol, expiry: committed.append((symbol, expiry)),
        signal_refresh=lambda: None,
        live_feed_enabled=lambda: False,
        live_feed_provider=lambda: "NSE_BSE",
        restart_feed=lambda *args: restarted.append(args),
    )

    assert switcher.switch("NIFTY", "new") is True
    assert committed == [("NIFTY", "new")]
    assert restarted == []
