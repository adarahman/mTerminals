import asyncio
import json

from server.websocket_handshake import WebSocketHandshakeSender


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _WebSocket:
    def __init__(self):
        self.messages = []

    async def send_str(self, value):
        self.messages.append(json.loads(value))


def _sender(payload=lambda: {"symbol": "NIFTY"}):
    return WebSocketHandshakeSender(
        encode=json.dumps,
        market_lock=_Lock(),
        market_payload=payload,
        baseline_version=lambda: 7,
        index_quotes=lambda: {"NIFTY": {"ltp": 25000}},
        pipeline_status=lambda: {"status": "LIVE"},
        funds=lambda: {"available": 1000},
        algo_status=lambda: {"enabled": False},
        reconciliation_alert=lambda: {"reason": "mismatch"},
        paper_snapshot=lambda: ({"pnl": 10}, [{"order_id": "O1"}]),
    )


def test_sends_complete_handshake_in_stable_order():
    websocket = _WebSocket()
    asyncio.run(_sender().send(websocket, send_full=True))

    assert [message["type"] for message in websocket.messages] == [
        "full",
        "indexQuotes",
        "pipelineStatus",
        "funds",
        "algoStatus",
        "reconciliationAlert",
        "portfolio",
        "orders",
    ]
    assert websocket.messages[0]["version"] == 7


def test_does_not_send_stale_or_absent_optional_snapshots():
    websocket = _WebSocket()
    sender = WebSocketHandshakeSender(
        encode=json.dumps,
        market_lock=_Lock(),
        market_payload=lambda: None,
        baseline_version=lambda: 1,
        index_quotes=lambda: {},
        pipeline_status=lambda: {"status": "STARTING"},
        funds=lambda: None,
        algo_status=lambda: {},
        reconciliation_alert=lambda: None,
        paper_snapshot=lambda: ({}, []),
    )

    asyncio.run(sender.send(websocket, send_full=True))

    assert [message["type"] for message in websocket.messages] == [
        "pipelineStatus",
        "algoStatus",
        "portfolio",
        "orders",
    ]
