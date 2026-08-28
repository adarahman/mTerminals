import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]

# Modules from the pre-migration flat `backend/` layout. They were folded into
# the layered `src/` architecture (brokers/*, application/market_pipeline,
# server/feeds, core/ports, ...). Nothing under src/ may import them and the
# files themselves must no longer exist — this pins the migration as complete
# and blocks accidental reintroduction.
LEGACY_MODULE_NAMES = {
    "ws_server_live",
    "broker_pipeline",
    "broker_market_pipeline",
    "tick_pipeline",
    "smartapi_pipeline_adapter",
    "smartapi_feed_adapter",
    "market_api",
    "option_chain_json",
    "mTerminals_json",
    "dashboard_serializer",
    "expiry_manager",
    "index_contributors",
    "pipeline_config",
    "engine",
}

# Temporary outer-edge seams only. These modules translate or compose legacy
# runtime behavior; no domain/application module may be added to this list.
ALLOWED_LEGACY_SEAMS = {
}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def assert_layer_excludes(layer: str, prohibited: set[str]) -> None:
    violations = []
    for path in sorted((BACKEND / layer).rglob("*.py")):
        bad = imported_roots(path) & prohibited
        if bad:
            violations.append(f"{path.relative_to(BACKEND)} -> {', '.join(sorted(bad))}")
    assert not violations, "architecture dependency violation(s):\n" + "\n".join(violations)


def test_legacy_backend_modules_are_removed():
    found = [
        str(path.relative_to(BACKEND))
        for path in BACKEND.rglob("*")
        if path.name in LEGACY_MODULE_NAMES
    ]
    assert not found, "legacy modules still present under src/: " + ", ".join(sorted(found))


def test_legacy_backend_modules_are_not_imported():
    violations = []
    for path in sorted(BACKEND.rglob("*.py")):
        bad = imported_roots(path) & LEGACY_MODULE_NAMES
        if bad:
            violations.append(f"{path.relative_to(BACKEND)} -> {', '.join(sorted(bad))}")
    assert not violations, "legacy imports still present:\n" + "\n".join(violations)


def test_active_source_does_not_document_the_deleted_server_filename():
    legacy_filename = "ws_server" + "_live.py"
    violations = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.parts:
            continue
        if legacy_filename in path.read_text(encoding="utf-8"):
            violations.append(str(path.relative_to(BACKEND)))
    assert not violations, "stale legacy server references: " + ", ".join(violations)


def test_composition_root_does_not_own_websocket_security_policy():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    assert "import ipaddress" not in app
    assert "def _peer_is_loopback" not in app
    assert "def _host_is_loopback" not in app
    assert "def _origin_allowed" not in app


def test_composition_root_does_not_build_paper_price_maps():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    assert "def _build_current_prices" not in app


def test_composition_root_has_no_provider_specific_feed_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    for provider in ("smartapi", "upstox", "shoonya", "kotak"):
        assert f"def start_{provider}_feed" not in app
        assert f"def restart_{provider}_feed" not in app
        assert f"def _switch_{provider}_symbol_blocking" not in app
        assert f"def _stop_{provider}_feed_blocking" not in app


def test_composition_root_has_no_live_order_gateway_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    for helper in (
        "_live_order_gate",
        "_check_live_rate_limit",
        "_live_trading_kill_switch_active",
        "_completed_live_order",
        "_submit_live_order_idempotent",
    ):
        assert f"def {helper}" not in app


def test_composition_root_has_no_daily_scheduler_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    for helper in ("_live_aggregators", "_reset_daily_sessions", "_maybe_trigger_eod"):
        assert f"def {helper}" not in app


def test_composition_root_has_no_background_state_setter_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    for helper in ("_set_last_funds", "_set_last_live_positions", "_set_last_algo_status"):
        assert f"def {helper}" not in app


def test_composition_root_has_no_market_cycle_service_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    for helper in (
        "_collect_pipeline_payload",
        "_seed_oi_baselines",
        "_publish_canonical_payload",
        "_pace_until_next_tick",
    ):
        assert f"def {helper}" not in app


def test_composition_root_has_no_websocket_service_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    for helper in (
        "_send_handshake_snapshot",
        "_ws_dispatch_message",
        "_set_price_source",
        "_set_futures_expiry",
    ):
        assert f"def {helper}" not in app


def test_composition_root_has_no_runtime_payload_state_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    for helper in (
        "_store_canonical_payload",
        "_store_previous_payload",
        "_invalidate_market_baseline",
    ):
        assert f"def {helper}" not in app


def test_composition_root_has_no_engine_cycle_wrapper():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    assert "def engine_loop" not in app


def test_composition_root_has_no_print_logging_wrapper():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    assert "def _print_log" not in app


def test_composition_root_has_no_analytics_runner_wrapper():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    assert "def run_pipeline_once" not in app
    assert "def _run_pipeline_locked" not in app


def test_composition_root_has_no_index_quote_fetch_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    for helper in (
        "_index_quote_fetcher",
        "fetch_nse_index_quotes_sync",
        "fetch_bse_index_quote_sync",
        "fetch_index_quotes_smartapi_sync",
    ):
        assert f"def {helper}" not in app


def test_composition_root_has_no_dashboard_bridge_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    for helper in ("broadcast_bridge", "bridge_ws_handler", "bridge_loop"):
        assert f"def {helper}" not in app


def test_composition_root_has_no_http_route_handler_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    for helper in (
        "ws_handler",
        "spot_history_handler",
        "history_handler",
        "backtest_handler",
        "lot_sizes_handler",
        "metrics_handler",
        "health_handler",
    ):
        assert f"def {helper}" not in app


def test_composition_root_has_no_market_selection_service_wrappers():
    app = (BACKEND / "server" / "app.py").read_text(encoding="utf-8")
    assert "def switch_symbol" not in app
    assert "def switch_data_source" not in app


def test_brokers_do_not_depend_on_decision_strategy_or_risk():
    assert_layer_excludes("brokers", {"decision", "strategy", "risk"})


def test_core_does_not_depend_on_outer_layers():
    assert_layer_excludes(
        "core",
        {
            "analytics",
            "application",
            "brokers",
            "decision",
            "infrastructure",
            "market_api",
            "risk",
            "server",
            "storage",
            "strategy",
        },
    )


def test_quote_domain_does_not_depend_on_brokers_or_server():
    assert_layer_excludes(
        "market/quotes",
        {"application", "brokers", "decision", "risk", "server", "strategy"},
    )


def test_instrument_domain_does_not_depend_on_brokers_or_server():
    assert_layer_excludes(
        "market/instruments",
        {"application", "brokers", "decision", "risk", "server", "strategy"},
    )


def test_option_chain_domain_does_not_depend_on_brokers_or_server():
    assert_layer_excludes(
        "market/option_chain",
        {"application", "brokers", "decision", "risk", "server", "strategy"},
    )


def test_execution_domain_does_not_depend_on_brokers_or_server():
    assert_layer_excludes(
        "execution",
        {"application", "brokers", "decision", "server"},
    )


def test_storage_does_not_depend_on_domain_decisions():
    assert_layer_excludes("storage", {"analytics", "decision", "oi", "risk", "strategy"})


def test_analytics_do_not_depend_on_paper_or_execution_state():
    assert_layer_excludes("analytics", {"execution", "risk", "strategy"})
    assert_layer_excludes("oi", {"execution", "risk", "strategy"})
