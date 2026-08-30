from types import SimpleNamespace

from server import runtime_stack


def test_runtime_stack_composes_services_and_installs_dashboard(monkeypatch):
    calls = []
    supervisor = SimpleNamespace(
        publish_reconciliation_alert=lambda *_args: None,
        build_status=lambda: {},
    )
    live = SimpleNamespace(
        position_reconciler=object(),
        supervisor=supervisor,
        auto_executor=object(),
        submission=SimpleNamespace(handle=lambda _payload: None),
    )
    market = SimpleNamespace(
        funds=SimpleNamespace(start=lambda: None, stop=lambda: None),
        index_quotes=SimpleNamespace(run=lambda: None),
        algo_status=SimpleNamespace(run=lambda: None),
        reconciliation=SimpleNamespace(run=lambda: None),
    )
    dashboard = SimpleNamespace(
        handshake=object(),
        message_router=object(),
        query_controller=object(),
        handler=object(),
    )
    application = object()
    monkeypatch.setattr(
        runtime_stack,
        "build_live_trading_runtime",
        lambda **kwargs: calls.append(("live", kwargs)) or live,
    )
    monkeypatch.setattr(
        runtime_stack,
        "build_market_runtime",
        lambda **kwargs: calls.append(("market", kwargs)) or market,
    )
    monkeypatch.setattr(
        runtime_stack,
        "build_dashboard_transport",
        lambda **kwargs: calls.append(("dashboard", kwargs)) or dashboard,
    )
    monkeypatch.setattr(
        runtime_stack,
        "build_server_application",
        lambda **kwargs: calls.append(("application", kwargs)) or application,
    )
    monkeypatch.setattr(
        runtime_stack,
        "configure_feed_orchestration",
        lambda **kwargs: calls.append(("feeds", kwargs)),
    )

    state = SimpleNamespace(
        MARKET_SELECTION=SimpleNamespace(symbol="NIFTY"),
        LAST_PAYLOAD=None,
        LAST_LIVE_POSITIONS=None,
    )
    core = SimpleNamespace(
        broadcast=lambda _message: None,
        paper_portfolio=SimpleNamespace(
            broadcast=lambda _prices: None,
            handshake_snapshot=lambda: ({}, []),
            broadcast_from_feed=lambda _prices: None,
        ),
        analytics=SimpleNamespace(run=lambda: None),
        symbol_switcher=SimpleNamespace(switch=lambda *_args: None),
        data_source_switcher=SimpleNamespace(switch=lambda *_args: None),
        bridge=object(),
    )
    brokers = SimpleNamespace(
        resolve_option_contract=None,
        market_data=SimpleNamespace(find_option_token=lambda *_args: None),
        place_order=lambda *_args: None,
        get_positions=lambda: [],
        get_order_book=lambda: [],
        get_funds=lambda: {},
        BROKER_SERVICES_ENABLED=False,
        SMARTAPI_INDEX_TOKENS={},
        get_candle_data=lambda *_args: None,
        get_index_candles=lambda *_args: None,
    )
    feed_manager = SimpleNamespace(
        _create_background_task=lambda *_args: None,
        _feed_allowed=lambda _provider: False,
    )
    paper_engine = SimpleNamespace(cancel_order=lambda _order_id: False)

    stack = runtime_stack.build_runtime_stack(
        runtime_state=state,
        core_runtime=core,
        live_trading_config=SimpleNamespace(enabled=False),
        paper_engine=paper_engine,
        paper_price_book=SimpleNamespace(build=lambda _payload: {}),
        eod_trigger_time=object(),
        position_reconcile_seconds=120,
        host="127.0.0.1",
        http_port=5500,
        middleware=object(),
        origin_allowed=lambda *_args: True,
        encode=str,
        decode=lambda value: value,
        broker_services=brokers,
        broker_settings=SimpleNamespace(execution_broker="SMARTAPI"),
        feed_manager=feed_manager,
        logger=object(),
        report=lambda *_args: None,
        run_backtest_call=lambda *_args: None,
    )

    assert stack.live_trading is live
    assert stack.market is market
    assert stack.dashboard is dashboard
    assert stack.application is application
    assert state.WS_HANDSHAKE is dashboard.handshake
    assert [name for name, _kwargs in calls] == [
        "live",
        "market",
        "dashboard",
        "application",
        "feeds",
    ]
