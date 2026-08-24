import pandas as pd

from application.payload_builders.greeks import build_greeks_rows


def test_build_greeks_rows_preserves_real_engine_columns():
    frame = pd.DataFrame(
        [{"Strike": 25000, "iv": 14.126, "cDelta": 0.51234, "netGEX": -12.34567}]
    )

    rows = build_greeks_rows(frame)

    assert rows[0]["strike"] == 25000
    assert rows[0]["iv"] == 14.13
    assert rows[0]["cDelta"] == 0.5123
    assert rows[0]["netGEX"] == -12.3457
    assert rows[0]["pGamma"] is None


def test_build_greeks_rows_handles_missing_table():
    assert build_greeks_rows(None) == []
