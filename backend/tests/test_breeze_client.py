import importlib

import pytest


class FakeApi:
    def __init__(self):
        self.session_calls = []
        self.place_calls = []
        self.orders = []
        self.positions = []
        self.funds = {
            "cash_limit": 100000.0, "amount_allocated": 20000.0,
            "block_by_trade": 5000.0, "isec_margin": 0.0,
        }

    def generate_session(self, **kwargs):
        self.session_calls.append(kwargs)

    def get_order_list(self, **kwargs):
        return {"Success": self.orders, "Status": 200, "Error": None}

    def get_portfolio_positions(self):
        return {"Success": self.positions, "Status": 200, "Error": None}

    def get_funds(self):
        return {"Success": self.funds, "Status": 200, "Error": None}

    def place_order(self, **kwargs):
        self.place_calls.append(kwargs)
        return {"Success": {"order_id": "42", "message": "ok"}, "Status": 200, "Error": None}


@pytest.fixture
def breeze(monkeypatch):
    module = importlib.import_module("brokers.breeze_client")
    values = {
        "breeze_api_key": "KEY",
        "breeze_api_secret": "SECRET",
        "breeze_api_session": "SESSION",
    }
    originals = {name: getattr(module.settings, name) for name in values}
    for name, value in values.items():
        object.__setattr__(module.settings, name, value)
    api = FakeApi()
    monkeypatch.setattr(module, "_session", module.BreezeSession(lambda: api))
    module._CONTRACT_CACHE.clear()
    yield module, api
    for name, value in originals.items():
        object.__setattr__(module.settings, name, value)


def test_session_generated_with_configured_credentials(breeze):
    module, api = breeze
    module._session.ensure_session()
    call = api.session_calls[0]
    assert call["api_secret"] == "SECRET"
    assert call["session_token"] == "SESSION"


def test_resolve_option_contract_returns_synthetic_key(breeze):
    module, _ = breeze
    resolved = module.resolve_option_contract("NIFTY", "28-Aug-2025", 24800, "CE")
    assert resolved == ("NFO", "NIFTY28AUG2524800CE", "")
    cached = module._CONTRACT_CACHE["NIFTY28AUG2524800CE"]
    assert cached["stock_code"] == "NIFTY"
    assert cached["expiry_date"] == "2025-08-28T06:00:00.000Z"
    assert cached["strike_price"] == "24800"
    assert cached["right"] == "Call"


def test_place_order_requires_prior_resolution(breeze):
    module, api = breeze
    with pytest.raises(module.BrokerError):
        module.place_order("NEVER_RESOLVED", "", "NFO", "BUY", 75)
    assert api.place_calls == []


def test_place_order_uses_cached_contract_fields(breeze):
    module, api = breeze
    key = module.resolve_option_contract("NIFTY", "28-Aug-2025", 24800, "CE")[1]
    order_id = module.place_order(
        key, "", "NFO", "BUY", 75, order_type="MARKET", order_tag="client-order-1",
    )
    assert order_id == "42"
    call = api.place_calls[0]
    assert call["stock_code"] == "NIFTY"
    assert call["action"] == "buy"
    assert call["order_type"] == "market"
    assert call["right"] == "Call"
    assert call["strike_price"] == "24800"
    assert call["user_remark"] == "client-order-1"


def test_existing_order_tag_prevents_duplicate_submission(breeze):
    module, api = breeze
    key = module.resolve_option_contract("NIFTY", "28-Aug-2025", 24800, "CE")[1]
    api.orders = [{"order_id": "already", "user_remark": "same"}]
    order_id = module.place_order(key, "", "NFO", "SELL", 75, order_tag="same")
    assert order_id == "already"
    assert api.place_calls == []


def test_account_payloads_are_normalized(breeze):
    module, api = breeze
    api.positions = [{
        "stock_code": "NIFTY", "quantity": "75",
        "realized_profit": "10", "unrealized_profit": "5",
    }]
    position = module.get_positions()[0]
    assert position["tradingsymbol"] == "NIFTY"
    assert position["netqty"] == 75
    assert position["pnl"] == 15

    funds = module.get_funds()
    assert funds["available_cash"] == 100000.0
    assert funds["utilised_margin"] == 5000.0
    assert funds["available_margin"] == 95000.0


def test_order_book_normalizes_breeze_field_names(breeze):
    module, api = breeze
    api.orders = [{
        "order_id": "1", "stock_code": "NIFTY", "status": "Executed",
        "action": "Buy", "quantity": "75", "user_remark": "tag-1",
        "order_datetime": "13-May-2024 14:28:02",
    }]
    row = module.get_order_book()[0]
    assert row["orderid"] == "1"
    assert row["tradingsymbol"] == "NIFTY"
    assert row["orderstatus"] == "Executed"
    assert row["transactiontype"] == "BUY"
    assert row["filledshares"] == "75"
    assert row["ordertag"] == "tag-1"
