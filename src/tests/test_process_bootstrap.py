from pathlib import Path
from types import SimpleNamespace

from server import process_bootstrap


def test_process_bootstrap_sequences_configuration_state_and_security(monkeypatch):
    calls = []
    args = SimpleNamespace()
    startup = SimpleNamespace(
        host="127.0.0.1",
        http_port=5500,
        feed_summary="feed summary",
        portfolio_summary="portfolio summary",
    )
    runtime = SimpleNamespace(paper_engine=object())
    live = SimpleNamespace(report=lambda emit: emit("live summary"))
    monkeypatch.setattr(
        process_bootstrap,
        "configure_lot_size_resolver",
        lambda resolver: calls.append(("lot-size", resolver)),
    )
    monkeypatch.setattr(
        process_bootstrap,
        "configure_startup",
        lambda **kwargs: calls.append(("startup", kwargs)) or startup,
    )
    monkeypatch.setattr(
        process_bootstrap,
        "initialize_runtime_state",
        lambda **kwargs: calls.append(("runtime", kwargs)) or runtime,
    )
    monkeypatch.setattr(
        process_bootstrap.LiveTradingConfig,
        "from_environment",
        lambda *_args: calls.append(("live", None)) or live,
    )
    monkeypatch.setattr(
        process_bootstrap,
        "build_allowed_origins",
        lambda *args: calls.append(("origins", args)) or {"http://localhost:5500"},
    )
    emitted = []
    broker_services = SimpleNamespace(
        BROKER_SERVICES_ENABLED=True,
        md_set_active_provider=lambda _provider: None,
    )
    def resolver(_symbol):
        return 75

    result = process_bootstrap.bootstrap_process(
        project_root=Path("/srv/mterminals"),
        runtime_state=object(),
        broker_services=broker_services,
        broker_settings=SimpleNamespace(live_feed_provider="KOTAK"),
        instrument_key=lambda *_args: "key",
        lot_size_resolver=resolver,
        supports_websocket=lambda _provider: True,
        environment={"ALLOWED_ORIGINS": "https://terminal.example"},
        parse_args=lambda: (args, ["--host-argument"]),
        emit=emitted.append,
    )

    assert result.args is args
    assert result.host_process_args == ["--host-argument"]
    assert result.startup is startup
    assert result.runtime is runtime
    assert result.live_trading is live
    assert result.allowed_origins == {"http://localhost:5500"}
    assert emitted == ["feed summary", "portfolio summary", "live summary"]
    assert calls[0] == ("lot-size", resolver)
    assert [name for name, _payload in calls] == [
        "lot-size",
        "startup",
        "runtime",
        "live",
        "origins",
    ]
