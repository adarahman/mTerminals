from datetime import datetime, timedelta, timezone

import pandas as pd

from analytics.oversold_oi_support import (
    evaluate_oversold_oi_support,
    reset_spot_rsi_history,
    update_spot_rsi,
)


def test_minute_rsi_waits_for_enough_history_then_detects_oversold():
    reset_spot_rsi_history()
    start = datetime(2026, 9, 3, 9, 15, tzinfo=timezone.utc)
    result = None
    for offset in range(15):
        result = update_spot_rsi(
            "NIFTY", 24_000 - offset * 10,
            (start + timedelta(minutes=offset)).isoformat(),
        )
    assert result == 0.0


def test_oversold_requires_support_confirmation_and_is_capped():
    master = pd.DataFrame([{"strike": 23_900, "pe_signal": "Writing BuildUp"}])
    result = evaluate_oversold_oi_support(
        rsi=24.0, spot=23_950, pe_wall=23_900, master=master,
        fut_signal="", strike_step=50,
    )
    assert result["state"] == "confirmed"
    assert result["score"] == 1.0


def test_put_buying_or_broken_wall_invalidates_oversold_confirmation():
    buying = pd.DataFrame([{"strike": 23_900, "pe_signal": "Buying BuildUp"}])
    result = evaluate_oversold_oi_support(
        rsi=25.0, spot=23_950, pe_wall=23_900, master=buying,
        fut_signal="Short Covering", strike_step=50,
    )
    assert result["state"] == "invalidated"
    assert result["score"] == 0.0

    broken = evaluate_oversold_oi_support(
        rsi=25.0, spot=23_850, pe_wall=23_900, master=None,
        fut_signal="Short Covering", strike_step=50,
    )
    assert broken["state"] == "invalidated"
    assert broken["score"] == 0.0

