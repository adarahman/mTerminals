from types import SimpleNamespace

import application.payload_builders.decision as decision_builder


def test_build_decision_returns_degraded_contract_on_engine_failure(monkeypatch):
    class BrokenDecisionEngine:
        def evaluate(self, engine_result, context):
            raise RuntimeError("unavailable")

    monkeypatch.setattr(decision_builder, "DecisionEngine", BrokenDecisionEngine)

    payload = decision_builder.build_decision(
        SimpleNamespace(), {}, "2026-08-24T10:00:00", "NIFTY", "2026-08-27"
    )

    assert payload["degraded"] is True
    assert payload["stateVersion"] == "NIFTY:2026-08-27:2026-08-24T10:00:00"
    assert payload["missingInputs"] == ["decision_engine"]
    assert payload["activeSignals"] == [{"text": "unavailable", "severity": "warn"}]
