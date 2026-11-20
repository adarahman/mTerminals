import asyncio
import json
from types import SimpleNamespace

from server.market_history_api import MarketHistoryApi


def _response_json(response):
    return json.loads(response.text)


def test_cash_history_uses_public_cache_when_broker_services_are_enabled():
    broker_calls = []
    public_calls = []
    api = MarketHistoryApi(
        state=lambda: {
            "symbol": "NIFTY",
            "broker_services_enabled": True,
            "index_tokens": {"NIFTY": {"exchange": "NSE", "token": "999"}},
        },
        get_candle_data=lambda *_args: broker_calls.append("spot") or [],
        get_index_candles=lambda *_args: broker_calls.append("history") or [],
        public_history=lambda *args, **kwargs: public_calls.append((args, kwargs))
        or [{"t": 1, "o": 2, "h": 3, "l": 1, "c": 2, "v": 4}],
    )
    request = SimpleNamespace(
        query={"symbol": "NIFTY", "range": "1m", "instrument": "INDEX"}
    )

    response = asyncio.run(api.history(request))

    assert broker_calls == []
    assert public_calls
    assert _response_json(response)[0]["c"] == 2
    assert response.headers["X-MTerminals-History-Source"] == "public-cache"


def test_contract_history_keeps_authorized_broker_path():
    broker_calls = []
    api = MarketHistoryApi(
        state=lambda: {
            "symbol": "NIFTY",
            "broker_services_enabled": True,
            "index_tokens": {"NIFTY": {"exchange": "NSE", "token": "999"}},
        },
        get_candle_data=lambda *_args: [],
        get_index_candles=lambda *_args: broker_calls.append("history") or [],
        public_history=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("public history must not serve futures")
        ),
    )
    request = SimpleNamespace(
        query={"symbol": "NIFTY", "range": "1d", "instrument": "FUT"}
    )

    response = asyncio.run(api.history(request))

    assert broker_calls == ["history"]
    assert _response_json(response) == []


def test_mcx_futures_history_uses_exact_expiry_and_isolated_cache():
    history_calls = []

    def get_commodity_candles(symbol, expiry, interval, fromdate, todate):
        history_calls.append((symbol, expiry, interval, fromdate, todate))
        return [{
            "timestamp": "2026-11-20T09:00:00",
            "open": 100, "high": 105, "low": 99, "close": 104, "volume": 10,
        }]

    api = MarketHistoryApi(
        state=lambda: {
            "symbol": "NIFTY",
            "broker_services_enabled": True,
            "index_tokens": {},
        },
        get_candle_data=lambda *_args: [],
        get_index_candles=lambda *_args: [],
        get_commodity_candles=get_commodity_candles,
    )
    request = SimpleNamespace(query={
        "symbol": "CRUDEOIL",
        "range": "1m",
        "instrument": "MCX_FUT",
        "expiry": "31-DEC-2026",
    })

    response = asyncio.run(api.history(request))
    next_expiry_response = asyncio.run(api.history(SimpleNamespace(query={
        **request.query,
        "expiry": "29-JAN-2027",
    })))

    assert response.status == 200
    assert response.headers["X-MTerminals-Instrument"] == "MCX_FUT"
    assert _response_json(response)[0]["c"] == 104
    assert _response_json(next_expiry_response)[0]["c"] == 104
    assert [call[:3] for call in history_calls] == [
        ("CRUDEOIL", "31-DEC-2026", "ONE_MINUTE"),
        ("CRUDEOIL", "29-JAN-2027", "ONE_MINUTE"),
    ]
