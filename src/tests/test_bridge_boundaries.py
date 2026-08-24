from unittest.mock import Mock

import pandas as pd

from server.bridge import DashboardBridge


def _bridge(fetcher):
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
        futures_fetcher=fetcher,
    )


def test_bridge_uses_injected_futures_capability():
    fetcher = Mock(
        return_value=pd.DataFrame(
            [{"LTP": 25100, "Change": 25, "PctChange": 0.1}]
        )
    )

    quote = _bridge(fetcher)._fetch_futures()

    fetcher.assert_called_once_with("NIFTY", "NEXT", True)
    assert quote["label"] == "NIFTY FUT (CUR)"
    assert quote["val"] == "25,100.00"
