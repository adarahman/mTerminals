"""Assembly boundary for recurring market and background runtime services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any

from application.market_cycle import (
    CanonicalPayloadPublisher,
    MarketEngineCycle,
    MarketTickPacer,
    OiBaselineSynchronizer,
)
from application.market_schedule import (
    DailyMarketScheduler,
    LiveFeedAggregatorRegistry,
)
from application.pipeline_execution import (
    MarketPipelineService,
)
from server.background_loops import (
    AlgoStatusLoop,
    FundsPoller,
    IndexQuoteLoop,
    NodeRelay,
    ReconciliationLoop,
)
from server.market_cycle_operations import MarketCycleOperations


@dataclass(frozen=True, slots=True)
class MarketRuntime:
    index_quotes: IndexQuoteLoop
    funds: FundsPoller
    reconciliation: ReconciliationLoop
    algo_status: AlgoStatusLoop
    node_relay: NodeRelay
    engine: MarketEngineCycle
    pipeline: MarketPipelineService
    scheduler: DailyMarketScheduler


def build_market_runtime(
    *,
    runtime_state: Any,
    market_data: Any,
    get_funds: Callable[..., Any],
    get_order_book: Callable[..., Any],
    get_positions: Callable[..., Any],
    position_reconciler: Any,
    position_reconcile_seconds: int,
    trading_supervisor: Any,
    auto_executor: Any,
    lot_sizes: Mapping[str, int],
    index_symbols: list[str],
    broadcast: Callable[[dict], Awaitable[Any]],
    report: Callable[..., Any],
    spawn_task: Callable[..., Any],
    active_feed_managers: Callable[[], dict],
    feed_allowed: Callable[[str], bool],
    fetch_all_eod: Callable[..., Any],
    record_today_flow: Callable[..., Any],
    eod_task_done: Callable[..., Any],
    flow_task_done: Callable[..., Any],
    reset_futures_session: Callable[[], Any],
    is_trading_day: Callable[..., bool],
    eod_trigger_time: time,
    run_pipeline: Callable[..., Awaitable[Any]],
    compute_diff: Callable[[Any, Any], Any],
    market_session_status: Callable[..., Any],
    paper_price_book: Any,
    paper_engine: Any,
    paper_portfolio: Any,
) -> MarketRuntime:
    index_quotes = IndexQuoteLoop(
        enabled=runtime_state.USE_INDEX_QUOTES,
        symbols=index_symbols,
        active_symbol=lambda: runtime_state.MARKET_SELECTION.symbol,
        get_spot_quote=market_data.get_spot_quote,
        broadcast=broadcast,
        index_quotes=runtime_state.INDEX_QUOTES,
        poll_seconds=runtime_state.INDEX_QUOTE_SECONDS,
        report=report,
        paused=lambda: runtime_state.MARKET_CYCLE_PAUSED,
    )
    funds = FundsPoller(
        get_funds=get_funds,
        broadcast=broadcast,
        set_last_funds=lambda value: setattr(runtime_state, "LAST_FUNDS", value),
        poll_seconds=runtime_state.FUNDS_POLL_SECONDS,
        spawn_task=spawn_task,
        report=report,
    )
    reconciliation = ReconciliationLoop(
        get_order_book=get_order_book,
        get_positions=get_positions,
        reconciler=position_reconciler,
        lot_sizes=lot_sizes,
        set_last_positions=lambda value: setattr(
            runtime_state, "LAST_LIVE_POSITIONS", value
        ),
        broadcast_alert=trading_supervisor.publish_reconciliation_alert,
        poll_seconds=position_reconcile_seconds,
        report=report,
    )
    algo_status = AlgoStatusLoop(
        build_status=trading_supervisor.build_status,
        broadcast=broadcast,
        set_last_status=lambda value: setattr(
            runtime_state, "LAST_ALGO_STATUS", value
        ),
        poll_seconds=runtime_state.ALGO_STATUS_POLL_SECONDS,
        report=report,
    )
    node_relay = NodeRelay(enabled=runtime_state.USE_RELAY, report=report)
    aggregators = LiveFeedAggregatorRegistry(managers=active_feed_managers)
    operations = MarketCycleOperations(
        pipeline_status=runtime_state.PIPELINE_STATUS,
        broadcast=broadcast,
        use_broker_services=lambda: runtime_state.USE_SMARTAPI,
        live_feed_provider=lambda: runtime_state.LIVE_FEED_PROVIDER,
        data_source=lambda: runtime_state.MARKET_SELECTION.data_source,
        feed_allowed=feed_allowed,
        fetch_all_eod=fetch_all_eod,
        record_today_flow=record_today_flow,
        eod_task_done=eod_task_done,
        flow_task_done=flow_task_done,
    )
    scheduler = DailyMarketScheduler(
        option_aggregators=aggregators.active,
        reset_futures_session=reset_futures_session,
        is_trading_day=is_trading_day,
        eod_trigger_time=eod_trigger_time,
        schedule_eod_jobs=operations.schedule_eod_jobs,
    )
    pipeline = MarketPipelineService(
        run_pipeline=run_pipeline,
        publish_status=operations.publish_pipeline_status,
        pipeline_status=runtime_state.PIPELINE_STATUS,
        timeout_seconds=runtime_state.PIPELINE_TIMEOUT_SECONDS,
        delayed_reason=operations.delayed_reason,
        delayed_overlay=operations.delayed_overlay,
    )
    oi_baselines = OiBaselineSynchronizer(aggregators=aggregators.active)
    publisher = CanonicalPayloadPublisher(
        stream_lock=runtime_state.MARKET_STREAM_LOCK,
        use_delta=lambda: runtime_state.USE_DELTA,
        previous_payload=lambda: runtime_state.LAST_SENT,
        store_payload=runtime_state.store_canonical_payload,
        store_previous_payload=runtime_state.store_previous_payload,
        broadcast=broadcast,
        compute_diff=compute_diff,
    )
    pacer = MarketTickPacer(
        poll_seconds=runtime_state.POLL_SECONDS,
        minimum_recompute_seconds=runtime_state.MIN_TICK_RECOMPUTE_SECONDS,
        symbol_switch_event=runtime_state.SYMBOL_SWITCH_EVENT,
        tick_activity_event=runtime_state.TICK_ACTIVITY_EVENT,
    )

    def schedule_auto_execution(decision):
        spawn_task(
            auto_executor.maybe_execute(
                decision,
                runtime_state.MARKET_SELECTION.symbol,
                runtime_state.MARKET_SELECTION.expiry,
            ),
            "auto_executor",
        )

    def schedule_node_relay(payload):
        spawn_task(node_relay.post(payload), "node_relay")

    engine = MarketEngineCycle(
        reset_daily_sessions=scheduler.reset_sessions,
        trigger_eod=scheduler.trigger_eod,
        collect_pipeline=pipeline.collect,
        observe_pipeline=lambda success, elapsed: (
            runtime_state.METRICS.observe_pipeline(success, elapsed)
        ),
        market_session_status=market_session_status,
        schedule_auto_execution=schedule_auto_execution,
        seed_oi_baselines=oi_baselines.synchronize,
        publish_payload=publisher.publish,
        schedule_node_relay=schedule_node_relay,
        connected_count=lambda: len(runtime_state.CONNECTED),
        build_current_prices=paper_price_book.build,
        check_pending_orders=paper_engine.check_pending_orders,
        broadcast_portfolio=paper_portfolio.broadcast,
        pace=pacer.wait,
        wait_until_running=runtime_state.MARKET_CYCLE_RESUME_EVENT.wait,
    )
    runtime_state.CANONICAL_PAYLOAD_PUBLISHER = publisher
    runtime_state.MARKET_TICK_PACER = pacer
    runtime_state.NODE_RELAY = node_relay
    runtime_state.MARKET_ENGINE_CYCLE = engine
    return MarketRuntime(
        index_quotes=index_quotes,
        funds=funds,
        reconciliation=reconciliation,
        algo_status=algo_status,
        node_relay=node_relay,
        engine=engine,
        pipeline=pipeline,
        scheduler=scheduler,
    )
