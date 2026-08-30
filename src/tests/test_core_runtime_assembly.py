import asyncio
from types import SimpleNamespace

from server import core_runtime_assembly


def test_core_runtime_assembles_dashboard_analytics_feeds_and_switchers(monkeypatch):
    monkeypatch.setattr(
        core_runtime_assembly,
        "build_feed_managers",
        lambda **_kwargs: {"SMARTAPI": object()},
    )
    selection = SimpleNamespace(
        symbol="NIFTY",
        expiry="01-Sep-2026",
        data_source="NSE_BSE",
        price_source="AUTO",
        futures_expiry="NEAR",
    )
    state = SimpleNamespace(
        MARKET_SELECTION=selection,
        USE_SMARTAPI=False,
        STRIKES_EACH_SIDE=50,
        LAST_PAYLOAD=None,
        INDEX_QUOTES={},
        SYMBOL_SWITCH_EVENT=asyncio.Event(),
        MAIN_LOOP=None,
        BASELINE_SEQ=0,
        BASELINE_ID=None,
        DASHBOARD_CLIENTS=SimpleNamespace(broadcast=lambda *_args, **_kwargs: None),
    )
    feed_manager = SimpleNamespace(
        _commit_symbol_selection=lambda *_args: None,
        _restart_live_feed=lambda *_args: None,
        _stop_active_broker_feed=lambda *_args: None,
        _commit_data_source=lambda *_args: None,
    )
    paper_engine = object()
    price_book = object()
    args = SimpleNamespace(
        extra_chains=False,
        strict_expiry=False,
        no_virtual_oi=False,
    )
    market_api = SimpleNamespace(fetch_public_futures=lambda *_args: None)

    runtime = core_runtime_assembly.build_core_runtime(
        runtime_state=state,
        args=args,
        paper_engine=paper_engine,
        paper_price_book=price_book,
        instrument_key=lambda *_args: "key",
        origin_allowed=lambda *_args: True,
        json_default=str,
        encode=str,
        market_api=market_api,
        broker_futures_fetcher=lambda *_args: None,
        activate_provider=lambda _provider: None,
        resolve_default_expiry=lambda _symbol: "01-Sep-2026",
        invoke_analytics=lambda *_args, **_kwargs: None,
        broker_services_enabled=False,
        provider_keys=("NSE_BSE",),
        supports_websocket=lambda _provider: False,
        feed_manager=feed_manager,
        report=lambda *_args: None,
        pipeline_timeout_seconds=8.0,
    )

    assert state.FEEDS == {"SMARTAPI": state.FEEDS["SMARTAPI"]}
    assert runtime.broadcast == runtime.broadcaster.broadcast
    assert runtime.paper_portfolio is not None
    assert runtime.bridge is not None
    assert runtime.analytics is not None
    assert runtime.symbol_switcher is not None
    assert runtime.data_source_switcher is not None
