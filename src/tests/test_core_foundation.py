from datetime import datetime, timezone
from decimal import Decimal
import importlib

from core import errors
from core.domain import (
    ActiveSignal,
    Decision,
    DecisionResult,
    Instrument,
    OptionChain,
    OptionContract,
)
from core.enums import Exchange, InstrumentType, OptionType, OrderStatus


def test_core_errors_is_canonical_exception_source():
    # The legacy `exceptions` shim was deleted; core.errors is the single
    # owner of all domain exceptions.
    assert issubclass(errors.MTerminalsError, Exception)
    for name in errors.__all__:
        assert hasattr(errors, name)
    assert hasattr(errors, "UpstoxError") and hasattr(errors, "KiteError")

    import pytest

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("exceptions")


def test_original_enum_names_remain_available_during_migration():
    assert OptionType.CE is OptionType.CALL
    assert OptionType.PE is OptionType.PUT
    assert InstrumentType.EQUITY.value == "EQUITY"
    assert OrderStatus.SENT is OrderStatus.SUBMITTED


def test_option_chain_is_available_from_core_domain():
    now = datetime.now(timezone.utc)
    underlying = Instrument("NIFTY", Exchange.NSE, InstrumentType.INDEX)
    option = Instrument(
        "NIFTY-CE",
        Exchange.NFO,
        InstrumentType.OPTION,
        expiry=now,
        strike=Decimal("25000"),
        option_type=OptionType.CALL,
    )
    contract = OptionContract(
        instrument=option,
        strike=Decimal("25000"),
        option_type=OptionType.CALL,
        expiry=now,
        ltp=Decimal("100"),
    )

    chain = OptionChain(underlying=underlying, expiry=now, contracts=[contract])

    assert chain.contracts == [contract]


def test_decision_types_are_compatibility_exports_of_core_domain():
    from decision.types import ActiveSignal as LegacyActiveSignal
    from decision.types import DecisionResult as LegacyDecisionResult

    assert LegacyActiveSignal is ActiveSignal
    assert LegacyDecisionResult is DecisionResult
    assert Decision is DecisionResult


def test_decision_serialization_contract_is_preserved():
    result = DecisionResult(decision_timestamp="2026-08-24T09:45:00+05:30")
    result.active_signals = [
        ActiveSignal("lower priority", "info", 20, "wall:ce"),
        ActiveSignal("canonical", "warn", 5, "wall:ce"),
    ]

    payload = result.to_dict()

    assert payload["bias"] == "NEUTRAL"
    assert payload["activeSignals"] == [
        {
            "id": "wall:ce",
            "text": "canonical",
            "severity": "warn",
            "priority": 5,
            "observedAt": "2026-08-24T09:45:00+05:30",
        }
    ]
