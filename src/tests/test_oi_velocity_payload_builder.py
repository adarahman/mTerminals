from types import SimpleNamespace

import pandas as pd

from application.payload_builders.oi_velocity import build_oi_velocity


def test_build_oi_velocity_groups_rows_by_dashboard_windows():
    frame = pd.DataFrame(
        [
            {"window": 5, "strike": 25000, "ce_oi": 100, "ce_doi": 10, "ce_ltp": 12.34,
             "pe_oi": 120, "pe_doi": -5, "pe_ltp": 14.56, "signal": "BUILDUP"},
            {"window": 15, "strike": 25100, "ceNow": 80, "peNow": 90},
        ]
    )

    result = build_oi_velocity(SimpleNamespace(vel_df=frame))

    assert [block["window"] for block in result] == [5, 15, 30]
    assert result[0]["rows"][0]["ceLTP"] == 12.3
    assert result[0]["rows"][0]["signal"] == "BUILDUP"
    assert result[1]["rows"][0]["strike"] == 25100
    assert result[2]["rows"] == []
