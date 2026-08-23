import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


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


def test_brokers_do_not_depend_on_decision_strategy_or_risk():
    assert_layer_excludes("brokers", {"decision", "strategy", "risk"})


def test_storage_does_not_depend_on_domain_decisions():
    assert_layer_excludes("storage", {"analytics", "decision", "oi", "risk", "strategy"})


def test_analytics_do_not_depend_on_paper_or_execution_state():
    assert_layer_excludes("analytics", {"paper_trading", "risk", "strategy"})
    assert_layer_excludes("oi", {"paper_trading", "risk", "strategy"})
