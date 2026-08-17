import importlib

import pytest


class FakeApi:
    def __init__(self):
        self.login_calls = []
        self.place_calls = []
        self.orders = []
        self.positions = []

    def login(self, **kwargs):
        self.login_calls.append(kwargs)
        return {"stat": "Ok", "susertoken": "token"}

    def get_order_book(self):
        return self.orders

    def get_positions(self):
        return self.positions

    def get_limits(self):
        return {"stat": "Ok", "cash": "10000", "marginused": "1200"}

    def place_order(self, **kwargs):
        self.place_calls.append(kwargs)
        return {"stat": "Ok", "norenordno": "42"}

    def searchscrip(self, **kwargs):
        return {
            "stat": "Ok",
            "values": [{
                "exchange": "NFO", "token": "123", "tsym": "NIFTY30JUL26C25000",
                "dname": "NIFTY 30JUL26 25000 CE",
            }],
        }


@pytest.fixture
def shoonya(monkeypatch):
    module = importlib.import_module("brokers.shoonya_client")
    values = {
        "shoonya_user_id": "USER",
        "shoonya_password": "PASS",
        "shoonya_totp_secret": "JBSWY3DPEHPK3PXP",
        "shoonya_vendor_code": "VENDOR",
        "shoonya_api_secret": "SECRET",
        "shoonya_imei": "DEVICE",
        "shoonya_product_type": "M",
    }
    originals = {name: getattr(module.settings, name) for name in values}
    for name, value in values.items():
        object.__setattr__(module.settings, name, value)
    api = FakeApi()
    monkeypatch.setattr(module, "_session", module.ShoonyaSession(lambda: api))
    yield module, api
    for name, value in originals.items():
        object.__setattr__(module.settings, name, value)


def test_login_uses_totp_and_configured_credentials(shoonya):
    module, api = shoonya
    module._session.ensure_session()
    call = api.login_calls[0]
    assert call["userid"] == "USER"
    assert call["vendor_code"] == "VENDOR"
    assert call["twoFA"].isdigit() and len(call["twoFA"]) == 6


def test_place_order_translates_shape_and_uses_tag(shoonya):
    module, api = shoonya
    order_id = module.place_order(
        "NIFTY30JUL26C25000", "123", "NFO", "BUY", 75,
        order_tag="client-order-1",
    )
    assert order_id == "42"
    assert api.place_calls == [{
        "buy_or_sell": "B", "product_type": "M", "exchange": "NFO",
        "tradingsymbol": "NIFTY30JUL26C25000", "quantity": 75,
        "discloseqty": 0, "price_type": "MKT", "price": 0.0,
        "trigger_price": None, "retention": "DAY", "amo": "NO",
        "remarks": "client-order-1",
    }]


def test_existing_order_tag_prevents_duplicate_submission(shoonya):
    module, api = shoonya
    api.orders = [{"stat": "Ok", "norenordno": "already", "remarks": "same"}]
    assert module.place_order("TSYM", "1", "NFO", "SELL", 25, order_tag="same") == "already"
    assert api.place_calls == []


def test_account_payloads_are_normalized(shoonya):
    module, api = shoonya
    api.positions = [{"tsym": "NIFTY30JUL26C25000", "netqty": "75", "rpnl": "10", "urmtom": "5"}]
    position = module.get_positions()[0]
    assert position["tradingsymbol"] == "NIFTY30JUL26C25000"
    assert position["pnl"] == 15
    assert module.get_funds()["available_cash"] == 10000
    assert module.get_funds()["utilised_margin"] == 1200


def test_exact_option_contract_resolution(shoonya):
    module, _ = shoonya
    assert module.resolve_option_contract("NIFTY", "30-Jul-2026", 25000, "CE") == (
        "NFO", "NIFTY30JUL26C25000", "123",
    )
