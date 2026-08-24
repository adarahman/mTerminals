from types import SimpleNamespace

import pandas as pd

from application.virtual_oi_service import enrich_virtual_oi


def test_disabled_virtual_oi_uses_confirmed_values():
    rows = [{"strike": 25000, "ceOI": 100, "peOI": 120}]

    result = enrich_virtual_oi(rows, "NIFTY", enabled=False)

    assert result is rows
    assert rows[0]["ceVirtualOI"] == 100
    assert rows[0]["peVirtualOI"] == 120
    assert rows[0]["ceVoiConf"] == 0.0
    assert rows[0]["peVoiDrift"] is False


def test_virtual_oi_uses_history_features_and_refreshes_changed_anchor():
    calls = []

    class Coordinator:
        _estimators = {
            "NIFTY_25000::CE": SimpleNamespace(last_confirmed_oi=90),
            "NIFTY_25000::PE": SimpleNamespace(last_confirmed_oi=120),
        }

        def dispatch_tick(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                virtual_oi_running=kwargs["confirmed_oi"] + 5,
                confidence_weight=0.876,
                drift_detected=True,
            )

        def on_nse_refresh(self, symbol, side, confirmed_oi):
            calls.append((symbol, side, confirmed_oi))

    snapshot = pd.DataFrame(
        [{"StrikePrice": 25000, "CE_Volume_Delta": 10, "PE_Volume_Delta": 20}]
    )
    rows = [{"strike": 25000, "ceOI": 100, "peOI": 120}]

    enrich_virtual_oi(
        rows,
        "NIFTY",
        SimpleNamespace(oi_history_snapshot=snapshot),
        coordinator=Coordinator(),
    )

    assert rows[0]["ceVirtualOI"] == 105
    assert rows[0]["peVirtualOI"] == 125
    assert rows[0]["ceVoiConf"] == 0.88
    assert ("NIFTY_25000", "CE", 100) in calls
    assert ("NIFTY_25000", "PE", 120) not in calls
