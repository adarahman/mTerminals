import asyncio

from server.websocket_query import WebSocketQueryController


def test_applies_normalized_query_controls_and_invalidates_baseline():
    state = {"symbol": "NIFTY", "price": "AUTO", "future": "NEAR"}
    calls = []

    async def switch_data_source(value):
        calls.append(("data", value))

    controller = WebSocketQueryController(
        current_symbol=lambda: state["symbol"],
        switch_symbol=lambda symbol, expiry: calls.append(("symbol", symbol, expiry)),
        switch_data_source=switch_data_source,
        current_price_source=lambda: state["price"],
        set_price_source=lambda value: state.__setitem__("price", value),
        current_futures_expiry=lambda: state["future"],
        set_futures_expiry=lambda value: state.__setitem__("future", value),
        invalidate_market_baseline=lambda: calls.append(("invalidate",)),
    )

    result = asyncio.run(
        controller.apply(
            {
                "symbol": "BANKNIFTY",
                "expiry": "31JUL2026",
                "dataSource": "UPSTOX",
                "priceSource": " fut ",
                "futuresExpiry": " next ",
            }
        )
    )

    assert state == {"symbol": "NIFTY", "price": "FUT", "future": "NEXT"}
    assert calls == [
        ("symbol", "BANKNIFTY", "31JUL2026"),
        ("data", "UPSTOX"),
        ("invalidate",),
        ("invalidate",),
    ]
    assert result.futures_reference_switched is True


def test_invalid_and_unchanged_controls_do_not_mutate_state():
    calls = []

    async def reject_data_source(_value):
        raise ValueError("unknown provider")

    controller = WebSocketQueryController(
        current_symbol=lambda: "NIFTY",
        switch_symbol=lambda *args: calls.append(args),
        switch_data_source=reject_data_source,
        current_price_source=lambda: "AUTO",
        set_price_source=lambda value: calls.append(("price", value)),
        current_futures_expiry=lambda: "NEAR",
        set_futures_expiry=lambda value: calls.append(("future", value)),
        invalidate_market_baseline=lambda: calls.append(("invalidate",)),
    )

    result = asyncio.run(
        controller.apply(
            {
                "dataSource": "INVALID",
                "priceSource": "AUTO",
                "futuresExpiry": "invalid",
            }
        )
    )

    assert calls == []
    assert result.futures_reference_switched is False
