from dataclasses import FrozenInstanceError

import pytest

from application.pipeline_config import RuntimeConfig as CanonicalRuntimeConfig


def test_runtime_config_is_owned_by_canonical_application_module():
    assert CanonicalRuntimeConfig.__module__ == "application.pipeline_config"


def test_runtime_config_preserves_partial_update_defaults():
    config = CanonicalRuntimeConfig(symbol=" nifty ")

    assert config.symbol == "NIFTY"
    assert config.expiry is None
    assert config.use_smartapi is None
    assert config.broker_enabled is None


def test_broker_enabled_is_the_clear_compatibility_view():
    config = CanonicalRuntimeConfig(use_smartapi=True)
    assert config.broker_enabled is True


def test_runtime_config_is_immutable_and_normalizes_enums():
    config = CanonicalRuntimeConfig(
        symbol="sensex",
        price_source="fut",
        futures_expiry="next",
        strikes_each_side=20,
    )

    assert config.symbol == "SENSEX"
    assert config.price_source == "FUT"
    assert config.futures_expiry == "NEXT"
    with pytest.raises(FrozenInstanceError):
        config.symbol = "NIFTY"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", " "),
        ("expiry", ""),
        ("strikes_each_side", 0),
        ("strikes_each_side", True),
        ("price_source", "CASH"),
        ("futures_expiry", "WEEKLY"),
        ("operation_timeout_seconds", 0),
    ],
)
def test_runtime_config_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        CanonicalRuntimeConfig(**{field: value})
