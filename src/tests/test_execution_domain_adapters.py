from decimal import Decimal

import pytest

from core.enums import Exchange, InstrumentType, OptionType, OrderStatus
from execution import order_from_paper_record, position_from_paper_record


def test_paper_order_maps_to_core_order_in_units():
    order = order_from_paper_record(
        {
            "id": "paper-1",
            "timestamp": 1770000000,
            "symbol": "NIFTY",
            "expiry": "25-Aug-2026",
            "strike": 25000,
            "instrument_type": "CE",
            "side": "BUY",
            "qty_lots": 2,
            "order_type": "MARKET",
            "limit_price": None,
            "status": "FILLED",
            "fill_price": 125.5,
            "fill_timestamp": 1770000001,
            "reject_reason": None,
        },
        lot_size=65,
    )

    assert order.quantity == 130
    assert order.status is OrderStatus.FILLED
    assert order.instrument.exchange is Exchange.NFO
    assert order.instrument.instrument_type is InstrumentType.OPTION
    assert order.instrument.option_type is OptionType.CALL
    assert order.instrument.strike == Decimal("25000")
    assert order.filled_price == Decimal("125.5")


def test_paper_position_preserves_signed_quantity_and_pnl():
    position = position_from_paper_record(
        {
            "symbol": "SENSEX",
            "expiry": "2026-08-27",
            "strike": 78000,
            "instrument_type": "PE",
            "net_qty_lots": -3,
            "avg_price": 200,
            "realized_pnl": 500,
            "unrealized_pnl": -750,
            "last_price": 205,
        },
        lot_size=20,
    )

    assert position.quantity == -60
    assert position.instrument.exchange is Exchange.BFO
    assert position.average_price == Decimal("200")
    assert position.realized_pnl == Decimal("500")
    assert position.unrealized_pnl == Decimal("-750")
    assert position.last_price == Decimal("205")


@pytest.mark.parametrize("lot_size", [0, -1])
def test_invalid_lot_size_fails_instead_of_guessing(lot_size):
    with pytest.raises(ValueError):
        position_from_paper_record(
            {
                "symbol": "NIFTY",
                "expiry": "",
                "strike": None,
                "instrument_type": "INDEX",
                "net_qty_lots": 1,
                "avg_price": 100,
            },
            lot_size=lot_size,
        )
