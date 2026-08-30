import json
import logging

import pytest

from logging_config import RedactSensitiveHeaders, StructuredFormatter, redact_sensitive_text


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        ("Headers: {'Authorization': 'Bearer jwt-secret'}", "jwt-secret"),
        ('{"X-PrivateKey":"private-secret"}', "private-secret"),
        ("api_key=api-secret&symbol=NIFTY", "api-secret"),
        ("token: feed-secret, retrying", "feed-secret"),
        ('{"password":"pass-secret","pin":"1234"}', "pass-secret"),
        ("client_code=account-secret", "account-secret"),
        ("totp_secret=otp-seed", "otp-seed"),
    ],
)
def test_redact_sensitive_text_covers_common_credential_shapes(message, secret):
    redacted = redact_sensitive_text(message)
    assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_redaction_does_not_hide_operational_symbol_tokens():
    message = "symboltoken=26000 option token count=42"
    assert redact_sensitive_text(message) == message


def test_redaction_is_idempotent_when_multiple_handlers_filter_a_record():
    once = redact_sensitive_text("Authorization=Bearer secret-value")
    assert "secret-value" not in once
    assert redact_sensitive_text(once) == once


def test_filter_redacts_formatted_record_arguments():
    record = logging.LogRecord(
        "sdk", logging.ERROR, __file__, 1, "request failed: %s", ({"token": "secret-value"},), None
    )
    assert RedactSensitiveHeaders().filter(record) is True
    assert "secret-value" not in record.getMessage()


def test_structured_formatter_emits_json_with_safe_operational_fields_only():
    record = logging.LogRecord(
        "mterminals.server", logging.WARNING, __file__, 1,
        "feed failed api_key=secret-value", (), None,
    )
    record.event = "health.transition"
    record.subsystem = "market_feed"
    record.status = "degraded"
    record.symbol = "NIFTY"
    record.unsafe_payload = {"password": "must-not-appear"}

    payload = json.loads(StructuredFormatter().format(record))

    assert payload["event"] == "health.transition"
    assert payload["status"] == "degraded"
    assert payload["symbol"] == "NIFTY"
    assert "secret-value" not in payload["message"]
    assert "unsafe_payload" not in payload


def test_structured_formatter_includes_uniform_broker_fields():
    record = logging.LogRecord("brokers.connection", logging.INFO, __file__, 1, "connected", (), None)
    record.event = "broker.connection"
    record.subsystem = "broker"
    record.provider = "SHOONYA"
    record.operation = "connection"
    record.status = "ready"

    payload = json.loads(StructuredFormatter().format(record))

    assert payload["provider"] == "SHOONYA"
    assert payload["operation"] == "connection"


def test_health_transition_logging_is_deduplicated(monkeypatch, caplog):
    from operational_metrics import OperationalMetrics
    from server import health_api, runtime_state
    snapshot = {
        "status": "degraded",
        "reasons": ["feed stale"],
        "websocket": {"connectedClients": 2},
        "marketFeed": {
            "status": "STALE", "reason": "feed stale", "symbol": "NIFTY",
            "expiry": "13-Aug-2026", "ageSeconds": 30.0,
        },
    }
    monkeypatch.setattr(runtime_state, "LAST_HEALTH_LOG_STATE", None)
    monkeypatch.setattr(runtime_state, "METRICS", OperationalMetrics())

    with caplog.at_level(logging.INFO, logger="mterminals.server"):
        health_api.log_health_transition(snapshot)
        health_api.log_health_transition(snapshot)

    matching = [r for r in caplog.records if getattr(r, "event", None) == "health.transition"]
    assert len(matching) == 1
    assert matching[0].status == "degraded"
    assert matching[0].connected_clients == 2
