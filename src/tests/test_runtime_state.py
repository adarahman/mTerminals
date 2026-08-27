import asyncio

from server import runtime_state


def test_runtime_state_owns_canonical_payload_storage(monkeypatch):
    payload = {"symbol": "NIFTY"}
    monkeypatch.setattr(runtime_state, "LAST_PAYLOAD", None)
    monkeypatch.setattr(runtime_state, "LAST_PAYLOAD_AT", None)
    monkeypatch.setattr(runtime_state, "LAST_SENT", None)

    runtime_state.store_canonical_payload(payload, 123.0)
    runtime_state.store_previous_payload(payload)

    assert runtime_state.LAST_PAYLOAD is payload
    assert runtime_state.LAST_PAYLOAD_AT == 123.0
    assert runtime_state.LAST_SENT is payload


def test_invalidating_market_baseline_clears_previous_payload_and_wakes_cycle(monkeypatch):
    monkeypatch.setattr(runtime_state, "LAST_SENT", {"symbol": "NIFTY"})
    monkeypatch.setattr(runtime_state, "SYMBOL_SWITCH_EVENT", asyncio.Event())

    runtime_state.invalidate_market_baseline()

    assert runtime_state.LAST_SENT is None
    assert runtime_state.SYMBOL_SWITCH_EVENT.is_set()
