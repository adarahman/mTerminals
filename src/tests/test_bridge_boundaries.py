from unittest.mock import Mock

import pandas as pd

from server.bridge import DashboardBridge


def _bridge(broker_fetcher, public_fetcher=None):
    public_fetcher = public_fetcher or Mock()
    return DashboardBridge(
        state=lambda: {
            "symbol": "NIFTY",
            "futures_expiry": "NEXT",
            "use_smartapi": True,
            "last_payload": None,
            "index_quotes": {},
        },
        origin_allowed=lambda _request: True,
        json_default=lambda value: value,
        market_api=Mock(),
        broker_futures_fetcher=broker_fetcher,
        public_futures_fetcher=public_fetcher,
    )


def test_bridge_uses_injected_futures_capability():
    fetcher = Mock(
        return_value=pd.DataFrame(
            [{"LTP": 25100, "Change": 25, "PctChange": 0.1}]
        )
    )

    quote = _bridge(fetcher)._fetch_futures()

    fetcher.assert_called_once_with("NIFTY", "NEXT")
    assert quote["label"] == "NIFTY FUT (CUR)"
    assert quote["val"] == "25,100.00"


def test_bridge_routes_public_futures_without_broker_fetch():
    broker_fetcher = Mock()
    public_fetcher = Mock(
        return_value=pd.DataFrame(
            [{"LTP": 25000, "Change": -10, "PctChange": -0.04}]
        )
    )
    bridge = _bridge(broker_fetcher, public_fetcher)
    bridge._state = lambda: {
        "symbol": "NIFTY",
        "futures_expiry": "NEAR",
        "use_smartapi": False,
        "last_payload": None,
        "index_quotes": {},
    }

    quote = bridge._fetch_futures()

    public_fetcher.assert_called_once_with("NIFTY", "NEAR")
    broker_fetcher.assert_not_called()
    assert quote["val"] == "25,000.00"
