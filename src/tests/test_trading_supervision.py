import asyncio
from types import SimpleNamespace

from risk.position_reconciler import Mismatch, ReconciliationResult
from server.trading_supervision import LiveTradingSupervisor


class _Guard:
    def get_status(self):
        return {"max_open_lots": 4}


class _Executor:
    def get_status(self, symbol):
        return {"symbol": symbol, "enabled": True}

    def get_history(self):
        return list(range(40))


def _supervisor(sent, stored):
    async def broadcast(message):
        sent.append(message)

    return LiveTradingSupervisor(
        account_guard=_Guard(),
        auto_executor=_Executor(),
        live_orders=SimpleNamespace(kill_switch_active=lambda: False),
        reconciler=SimpleNamespace(trip_lots=2),
        lot_sizes={"NIFTY": 25},
        cached_positions=lambda: None,
        symbol=lambda: "NIFTY",
        broker_label=lambda: "Angel One",
        live_trading_enabled=True,
        max_lots_per_order=1,
        max_orders_per_minute=5,
        store_alert=stored.append,
        broadcast=broadcast,
        clock=lambda: 123.0,
    )


def test_supervisor_builds_bounded_algo_status_history():
    supervisor = _supervisor([], [])

    status = supervisor.build_status()

    assert status["broker"] == "Angel One"
    assert status["accountGuard"]["current_open_lots"] is None
    assert status["autoExecutor"]["history"] == list(range(30))
    assert status["killSwitchActive"] is False


def test_supervisor_stores_and_broadcasts_same_reconciliation_payload():
    sent, stored = [], []
    supervisor = _supervisor(sent, stored)
    result = ReconciliationResult(
        mismatches=[
            Mismatch(
                symbol="NIFTY24OCT23000CE",
                order_book_lots=3,
                position_lots=1,
            )
        ],
        unparseable_symbols=[],
    )

    asyncio.run(supervisor.publish_reconciliation_alert(result, "periodic"))

    assert stored[0] is sent[0]["payload"]
    assert stored[0]["ts"] == 123.0
    assert stored[0]["tripped"] is True
