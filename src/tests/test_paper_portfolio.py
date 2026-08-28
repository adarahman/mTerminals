from execution.paper_trading import _instrument_key
import asyncio

from server.paper_portfolio import PaperPortfolioService, PaperPriceBook


def test_price_book_extracts_spot_future_and_option_legs():
    book = PaperPriceBook({}, _instrument_key)
    prices = book.build({
        "symbol": "NIFTY", "spot": 25000.0, "expiry": "01-Sep-2026",
        "futLTP": 25050.0,
        "chain": [{"strike": 25000, "ceLTP": 100.0, "peLTP": 90.0}],
    })
    assert prices[_instrument_key("NIFTY", "", None, "INDEX")] == 25000.0
    assert prices[_instrument_key("NIFTY", "01-Sep-2026", None, "FUT")] == 25050.0
    assert prices[_instrument_key("NIFTY", "01-Sep-2026", 25000, "CE")] == 100.0
    assert prices[_instrument_key("NIFTY", "01-Sep-2026", 25000, "PE")] == 90.0


def test_price_book_preserves_other_symbols_across_ticks():
    known = {_instrument_key("BANKNIFTY", "", None, "INDEX"): 58000.0}
    prices = PaperPriceBook(known, _instrument_key).build(
        {"symbol": "NIFTY", "spot": 25000.0}
    )
    assert len(prices) == 2
    assert prices[_instrument_key("BANKNIFTY", "", None, "INDEX")] == 58000.0


class _Engine:
    def __init__(self):
        self.checked = []

    def get_portfolio_summary(self, prices):
        return {"marked": dict(prices)}

    def get_fund_summary(self, *, spot_price, current_prices):
        return {"spot": spot_price, "count": len(current_prices)}

    def get_orders(self):
        return [{"id": "order-1"}]

    def check_pending_orders(self, prices):
        self.checked.append(dict(prices))


def test_portfolio_service_reuses_snapshot_for_handshake_and_broadcast():
    payload = {"symbol": "NIFTY", "spot": 25000.0}
    engine = _Engine()
    messages = []

    async def broadcast(message):
        messages.append(message)

    service = PaperPortfolioService(
        engine=engine,
        price_book=PaperPriceBook({}, _instrument_key),
        instrument_key=_instrument_key,
        broadcast=broadcast,
        last_payload=lambda: payload,
    )

    portfolio, orders = service.handshake_snapshot()
    asyncio.run(service.broadcast_from_feed(payload))

    assert portfolio["funds"]["spot"] == 25000.0
    assert orders == [{"id": "order-1"}]
    assert engine.checked
    assert [message["type"] for message in messages] == ["portfolio", "orders"]
