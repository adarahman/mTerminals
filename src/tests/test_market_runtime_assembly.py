import asyncio
from datetime import time
from types import SimpleNamespace

from server.market_runtime_assembly import build_market_runtime


class _Metrics:
    def observe_pipeline(self, _success, _elapsed):
        pass


class _Supervisor:
    async def publish_reconciliation_alert(self, *_args):
        pass

    def build_status(self):
        return {"enabled": False}


class _PriceBook:
    def build(self, _payload):
        return {}


class _PaperEngine:
    def check_pending_orders(self, _prices):
        return None


class _Portfolio:
    async def broadcast(self, _prices):
        pass


def test_market_runtime_installs_engine_transport_and_background_services():
    selection = SimpleNamespace(
        symbol="NIFTY", expiry="01-Sep-2026", data_source="KOTAK"
    )
    state = SimpleNamespace(
        USE_INDEX_QUOTES=True,
        INDEX_QUOTES={},
        INDEX_QUOTE_SECONDS=20,
        FUNDS_POLL_SECONDS=30,
        LAST_FUNDS=None,
        LAST_LIVE_POSITIONS=None,
        ALGO_STATUS_POLL_SECONDS=5,
        LAST_ALGO_STATUS=None,
        USE_RELAY=False,
        USE_SMARTAPI=True,
        LIVE_FEED_PROVIDER="KOTAK",
        MARKET_SELECTION=selection,
        PIPELINE_STATUS={"status": "STARTING", "reason": ""},
        PIPELINE_TIMEOUT_SECONDS=8,
        MARKET_STREAM_LOCK=asyncio.Lock(),
        USE_DELTA=True,
        LAST_SENT=None,
        POLL_SECONDS=10,
        MIN_TICK_RECOMPUTE_SECONDS=3,
        SYMBOL_SWITCH_EVENT=asyncio.Event(),
        TICK_ACTIVITY_EVENT=asyncio.Event(),
        CONNECTED=set(),
        METRICS=_Metrics(),
        store_canonical_payload=lambda *_args: None,
        store_previous_payload=lambda *_args: None,
    )

    async def broadcast(_message):
        pass

    runtime = build_market_runtime(
        runtime_state=state,
        market_data=SimpleNamespace(get_spot_quote=lambda _symbol: None),
        get_funds=lambda: {},
        get_order_book=lambda: [],
        get_positions=lambda: [],
        position_reconciler=object(),
        position_reconcile_seconds=120,
        trading_supervisor=_Supervisor(),
        auto_executor=SimpleNamespace(maybe_execute=lambda *_args: None),
        lot_sizes={},
        index_symbols=["NIFTY"],
        broadcast=broadcast,
        report=lambda *_args, **_kwargs: None,
        spawn_task=lambda *_args: None,
        active_feed_managers=lambda: {},
        feed_allowed=lambda _provider: True,
        fetch_all_eod=lambda *_args: None,
        record_today_flow=lambda: None,
        eod_task_done=lambda _task: None,
        flow_task_done=lambda _task: None,
        reset_futures_session=lambda: None,
        is_trading_day=lambda *_args: True,
        eod_trigger_time=time(15, 45),
        run_pipeline=lambda: None,
        compute_diff=lambda _old, _new: None,
        market_session_status=lambda *_args: "OPEN",
        paper_price_book=_PriceBook(),
        paper_engine=_PaperEngine(),
        paper_portfolio=_Portfolio(),
    )

    assert state.NODE_RELAY is runtime.node_relay
    assert state.MARKET_ENGINE_CYCLE is runtime.engine
    assert state.CANONICAL_PAYLOAD_PUBLISHER is not None
    assert state.MARKET_TICK_PACER is not None
    assert runtime.index_quotes is not None
    assert runtime.funds is not None
    assert runtime.reconciliation is not None
    assert runtime.algo_status is not None
