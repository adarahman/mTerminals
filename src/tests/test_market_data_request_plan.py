import pytest

from market.option_chain.requests import MarketDataRequestPlan


def _plan(**overrides):
    values = {
        "symbol": "NIFTY",
        "option_expiry": "25-Aug-2026",
        "option_exchange": "NSE",
        "strict_expiry": False,
        "futures_expiry": "NEAR",
        "broker_enabled": False,
    }
    values.update(overrides)
    return MarketDataRequestPlan(**values)


def test_request_plan_normalizes_bse_routing():
    plan = _plan(
        symbol=" sensex ",
        option_expiry=" 27-Aug-2026 ",
        option_exchange="bse",
        strict_expiry=True,
        futures_expiry="next",
        broker_enabled=True,
    )

    assert plan.symbol == "SENSEX"
    assert plan.option_expiry == "27-Aug-2026"
    assert plan.option_exchange == "BSE"
    assert plan.broker_derivatives_exchange == "BFO"
    assert plan.futures_expiry == "NEXT"


def test_request_plan_routes_nse_derivatives_to_nfo():
    assert _plan().broker_derivatives_exchange == "NFO"


@pytest.mark.parametrize("exchange", ["", "MCX"])
def test_request_plan_rejects_unknown_exchange(exchange):
    with pytest.raises(ValueError):
        _plan(option_exchange=exchange)
