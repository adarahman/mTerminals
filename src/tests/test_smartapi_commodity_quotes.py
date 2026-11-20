from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from brokers.smartapi.history import get_previous_close_oi
from brokers.smartapi.history import get_commodity_futures_candles
from brokers.smartapi.market_data import SmartApiMarketData


def test_commodity_history_resolves_the_selected_mcx_expiry(monkeypatch):
    contracts = {
        "NEAR": {"expiry": "25JUN2026", "token": "101"},
        "NEXT": {"expiry": "31JUL2026", "token": "102"},
        "FAR": {"expiry": "28AUG2026", "token": "103"},
    }
    resolved = []
    fetched = []
    monkeypatch.setattr(
        "brokers.smartapi.instruments.get_commodity_futures_contract",
        lambda underlying, which: resolved.append((underlying, which)) or contracts[which],
    )
    monkeypatch.setattr(
        "brokers.smartapi.history.fetch_candles_chunked",
        lambda *args: fetched.append(args) or [{
            "timestamp": "2026-07-01T09:00:00",
            "open": 294, "high": 296, "low": 293, "close": 295,
            "volume": 10,
        }],
    )

    result = get_commodity_futures_candles(
        "CRUDEOIL", "31-Jul-2026", "ONE_MINUTE",
        "2026-07-01 09:00", "2026-07-31 23:00",
    )

    assert result == [{
        "timestamp": "2026-07-01T09:00:00",
        "open": 294, "high": 296, "low": 293, "close": 295,
        "volume": 10,
    }]
    assert resolved == [("CRUDEOIL", "NEAR"), ("CRUDEOIL", "NEXT")]
    assert fetched == [(
        "MCX", "102", "ONE_MINUTE",
        "2026-07-01 09:00", "2026-07-31 23:00",
    )]


def test_previous_close_oi_uses_latest_daily_row_before_today(monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    captured = []

    def get_oi_data(exchange, token, interval, fromdate, todate):
        captured.append((exchange, token, interval, fromdate, todate))
        return [
            {"timestamp": (today - timedelta(days=2)).isoformat(), "oi": 80},
            {"timestamp": (today - timedelta(days=1)).isoformat(), "oi": 100},
            {"timestamp": today.isoformat(), "oi": 130},
        ]

    monkeypatch.setattr("brokers.smartapi.history.get_oi_data", get_oi_data)

    assert get_previous_close_oi("MCX", "123") == 100
    assert captured == [(
        "MCX",
        "123",
        "ONE_DAY",
        (today - timedelta(days=14)).strftime("%Y-%m-%d 00:00"),
        today.strftime("%Y-%m-%d 00:00"),
    )]


def test_commodity_quote_resolves_mcx_future_and_calculates_change(monkeypatch):
    future = {
        "symbol": "CRUDEOILFUT",
        "token": "123",
        "expiry": "31DEC2099",
        "lotsize": "100",
    }
    adapter = SmartApiMarketData()
    seen = []

    monkeypatch.setattr(
        "brokers.smartapi.instruments.get_commodity_futures_contract",
        lambda underlying, which: future,
    )

    def quotes(exchange, pairs, mode):
        seen.append((exchange, pairs, mode))
        return {
            "123": {
                "ltp": "110",
                "close": "100",
                "open": "102",
                "high": "112",
                "low": "99",
                "tradeVolume": "50",
                "opnInterest": "75",
            }
        }

    monkeypatch.setattr(adapter, "get_batch_quotes_by_token", quotes)

    result = adapter.get_commodity_futures_quote("CRUDEOIL")

    assert seen == [("MCX", [("CRUDEOILFUT", "123")], "FULL")]
    assert result["LTP"] == 110.0
    assert result["Change"] is None
    assert result["PctChange"] == 10.0
    assert result["Exchange"] == "MCX"
    assert result["LotSize"] == 100.0
    assert result["Expiry"] == "31-Dec-2099"


def test_commodity_option_chain_quotes_only_nearby_calls_and_puts(monkeypatch):
    adapter = SmartApiMarketData()
    monkeypatch.setattr(
        adapter,
        "get_commodity_futures_quote",
        lambda underlying: {"LTP": 6000.0, "LotSize": 100},
    )
    monkeypatch.setattr(
        "brokers.smartapi.instruments.get_commodity_option_contracts",
        lambda underlying, expiry, reference_price, strikes_around_atm: [
            {"strike": 6000.0, "type": "CE", "symbol": "CALL", "token": "10"},
            {"strike": 6000.0, "type": "PE", "symbol": "PUT", "token": "11"},
        ],
    )
    seen = []

    def quotes(exchange, pairs, mode):
        seen.append((exchange, pairs, mode))
        return {
            "10": {"ltp": "120", "opnInterest": "200", "tradeVolume": "30"},
            "11": {"ltp": "100", "opnInterest": "180", "tradeVolume": "25"},
        }

    monkeypatch.setattr(adapter, "get_batch_quotes_by_token", quotes)

    result = adapter.get_commodity_option_chain("CRUDEOIL", "31DEC2099")

    assert seen == [
        ("MCX", [("CALL", "10"), ("PUT", "11")], "FULL")
    ]
    assert result["atm_strike"] == 6000.0
    assert result["lot_size"] == 100
    assert [row["type"] for row in result["rows"]] == ["CE", "PE"]
