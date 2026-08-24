import pandas as pd

from application import market_snapshot_history as history


def test_compute_volume_changes_uses_nearest_eligible_snapshot():
    history.save_snapshots(
        [
            [
                "2026-08-24T10:00:00",
                {"25000": [100, 120, 40, 50]},
            ],
            [
                "2026-08-24T10:05:00",
                {"25000": [110, 130, 55, 72]},
            ],
        ]
    )
    master = pd.DataFrame(
        [{"strike": 25000, "ce_volume": 55, "pe_volume": 72}]
    )

    assert history.compute_volume_changes(master, 5) == {25000: (15, 22)}


def test_safe_number_replaces_nan_and_invalid_cells():
    assert history.safe_number(float("nan"), 7) == 7
    assert history.safe_number("invalid", 9) == 9
