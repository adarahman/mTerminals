from datetime import date

import pandas as pd

from market.option_chain.oi_change import PreviousCloseOiTracker


def test_seed_backs_out_previous_close_for_both_sides():
    frame = pd.DataFrame(
        [{"StrikePrice": 25000, "CE_OI": 120, "CE_ChgOI": 20, "PE_OI": 90, "PE_ChgOI": -10}]
    )
    tracker = PreviousCloseOiTracker(lambda *_args: frame)

    tracker.seed("NIFTY", "27-Aug-2026")

    assert tracker.anchors[("NIFTY", "27-Aug-2026", 25000, "CE")] == (date.today(), 100.0)
    assert tracker.anchors[("NIFTY", "27-Aug-2026", 25000, "PE")] == (date.today(), 100.0)
    assert tracker.change("NIFTY", "27-Aug-2026", 25000, "CE", 135) == 35.0


def test_change_falls_back_to_first_tick_when_public_seed_fails():
    tracker = PreviousCloseOiTracker(
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline"))
    )

    first = tracker.change("NIFTY", "27-Aug-2026", 25000, "CE", 120)
    second = tracker.change("NIFTY", "27-Aug-2026", 25000, "CE", 135)

    assert first == 0.0
    assert second == 15.0


def test_partial_seed_remains_retryable():
    frame = pd.DataFrame(
        [{"StrikePrice": 25000, "CE_OI": 120, "CE_ChgOI": 20, "PE_OI": None, "PE_ChgOI": None}]
    )
    tracker = PreviousCloseOiTracker(lambda *_args: frame)

    tracker.seed("NIFTY", "27-Aug-2026")

    state = tracker.seed_state[("NIFTY", "27-Aug-2026", date.today())]
    assert state["complete"] is False
