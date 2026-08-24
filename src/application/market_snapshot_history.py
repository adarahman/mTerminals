"""Short-lived in-memory market snapshots used for volume-change metrics."""

from datetime import datetime

import pandas as pd

from storage.caches import ListSlot

MAX_SNAPSHOT_AGE_MINUTES = 35
_SNAPSHOTS = ListSlot()


def load_snapshots():
    return _SNAPSHOTS.get()


def save_snapshots(snapshots):
    _SNAPSHOTS.set(snapshots)


def safe_number(value, default=0.0):
    """Return a finite numeric cell value or the supplied default."""
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(number) else number


def record_snapshot(master):
    """Store OI and volume values, pruning snapshots outside the window."""
    snapshots = load_snapshots()
    now = datetime.now()
    cutoff_seconds = MAX_SNAPSHOT_AGE_MINUTES * 60
    snapshots = [
        snapshot
        for snapshot in snapshots
        if (now - datetime.fromisoformat(snapshot[0])).total_seconds()
        <= cutoff_seconds
    ]
    values = {}
    for row in master.to_dict("records"):
        strike = str(int(safe_number(row.get("strike", 0))))
        values[strike] = [
            int(safe_number(row.get("ce_oi", 0))),
            int(safe_number(row.get("pe_oi", 0))),
            int(safe_number(row.get("ce_volume", 0))),
            int(safe_number(row.get("pe_volume", 0))),
        ]
    snapshots.append([now.isoformat(), values])
    save_snapshots(snapshots)


def find_snapshot_near(snapshots, window_minutes):
    """Return the snapshot whose age most closely matches the lookback."""
    if len(snapshots) < 2:
        return None
    now_timestamp = datetime.fromisoformat(snapshots[-1][0])
    target_age = window_minutes * 60
    best = None
    best_difference = float("inf")
    for iso_timestamp, snapshot in snapshots[:-1]:
        age = (now_timestamp - datetime.fromisoformat(iso_timestamp)).total_seconds()
        difference = abs(age - target_age)
        if difference < best_difference and age >= target_age * 0.5:
            best_difference = difference
            best = (iso_timestamp, snapshot)
    return best


def compute_volume_changes(master, window_minutes):
    """Return ``{strike: (ce_volume_change, pe_volume_change)}``."""
    found = find_snapshot_near(load_snapshots(), window_minutes)
    if found is None:
        return {}
    _, old_snapshot = found
    changes = {}
    for row in master.to_dict("records"):
        strike = str(int(safe_number(row.get("strike", 0))))
        ce_volume_now = int(safe_number(row.get("ce_volume", 0)))
        pe_volume_now = int(safe_number(row.get("pe_volume", 0)))
        previous = old_snapshot.get(
            strike, [0, 0, ce_volume_now, pe_volume_now]
        )
        ce_volume_old = previous[2] if len(previous) > 2 else ce_volume_now
        pe_volume_old = previous[3] if len(previous) > 3 else pe_volume_now
        changes[int(strike)] = (
            ce_volume_now - ce_volume_old,
            pe_volume_now - pe_volume_old,
        )
    return changes
