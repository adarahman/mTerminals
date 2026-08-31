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
