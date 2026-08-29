from types import SimpleNamespace

from server.application_assembly import build_server_application


def test_server_application_assembles_http_health_and_runtime_services():
    state = SimpleNamespace(
        MARKET_SELECTION=SimpleNamespace(symbol="NIFTY"),
        METRICS=object(),
        DASHBOARD_WS_HANDLER=object(),
    )
    feed_manager = SimpleNamespace(_create_background_task=lambda *_args: None)
    bridge = SimpleNamespace(handle=lambda _request: None, run=lambda: None)

    application = build_server_application(
        runtime_state=state,
        feed_manager=feed_manager,
        host="127.0.0.1",
        http_port=5500,
        middleware=object(),
        dashboard_websocket=state.DASHBOARD_WS_HANDLER,
        bridge=bridge,
        broker_services_enabled=False,
        index_tokens={"NIFTY": "99926000"},
        get_candle_data=lambda *_args, **_kwargs: None,
        get_index_candles=lambda *_args, **_kwargs: None,
        run_backtest_call=lambda *_args, **_kwargs: None,
        feed_allowed=lambda _provider: False,
        market_session_status=lambda *_args: "CLOSED",
        host_is_loopback=lambda _host: True,
        index_quotes=lambda: None,
        algo_status=lambda: None,
        reconcile=lambda: None,
        live_trading_enabled=False,
    )

    assert state.HTTP_ROUTE_HANDLERS is not None
    assert application.history_api is not None
    assert application.health_snapshot is not None
    assert application.http.host == "127.0.0.1"
    assert application.http.port == 5500
    assert application.services.runtime_state is state
    assert application.services.feed_manager is feed_manager
