import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]

LEGACY_MIXED_MODULES = {
    "engine",
    "expiry_manager",
    "index_contributors",
    "market_api",
    "mTerminals_json",
    "option_chain_json",
    "pipeline_config",
    "tick_pipeline",
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


def test_legacy_mixed_modules_are_quarantined_to_explicit_outer_seams():
    actual = {}
    architecture_roots = (
        "analytics",
        "application",
        "brokers",
        "core",
        "decision",
        "execution",
        "infrastructure",
        "market",
        "oi",
        "risk",
        "server",
        "storage",
        "strategy",
    )
    for root in architecture_roots:
        for path in sorted((BACKEND / root).rglob("*.py")):
            legacy = imported_roots(path) & LEGACY_MIXED_MODULES
            if legacy:
                actual[str(path.relative_to(BACKEND))] = legacy
    assert actual == ALLOWED_LEGACY_SEAMS


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
