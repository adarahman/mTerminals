"""
Volume Profile Analysis Module
-------------------------------
Computes Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL)
from tick-by-tick trade data.

Typical use cases:
- Session-based volume profile (e.g., one trading day)
- Rolling/anchored volume profile (custom start-end range)
- Composite profile across multiple sessions

Author: F&O mTerminals
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass


@dataclass
class VolumeProfileResult:
    poc: float                  # Point of Control price
    vah: float                  # Value Area High
    val: float                  # Value Area Low
    profile: pd.Series          # price_bin -> volume, sorted by price
    total_volume: float
    value_area_volume: float


def compute_volume_profile(
    ticks: pd.DataFrame,
    price_col: str = "price",
    volume_col: str = "volume",
    tick_size: float = 0.05,
    value_area_pct: float = 0.70,
) -> VolumeProfileResult:
    """
    Compute Volume Profile (POC, VAH, VAL) from tick data.

    Parameters
    ----------
    ticks : pd.DataFrame
        Tick data with at least price and volume columns.
        Should already be filtered to the session/range you want the profile for.
    price_col : str
        Column name containing trade price.
    volume_col : str
        Column name containing traded quantity/volume.
    tick_size : float
        Price bin size (bucket width). Use the instrument's tick size,
        or a coarser bucket (e.g. 0.5, 1, 5) for less granular profiles.
    value_area_pct : float
        Fraction of total volume to include in the value area (0.70 = 70%,
        the standard convention used by most platforms).

    Returns
    -------
    VolumeProfileResult
    """
    if ticks.empty:
        raise ValueError("ticks dataframe is empty")

    df = ticks[[price_col, volume_col]].copy()

    # Bucket prices into bins of size `tick_size`
    df["price_bin"] = (df[price_col] / tick_size).round() * tick_size

    # Aggregate volume per price bin
    profile = df.groupby("price_bin")[volume_col].sum().sort_index()

    total_volume = profile.sum()

    # --- POC: price bin with max volume ---
    poc_price = profile.idxmax()
    poc_idx = profile.index.get_loc(poc_price)

    # --- Value Area: expand outward from POC until value_area_pct of volume is covered ---
    target_volume = total_volume * value_area_pct

    accumulated = profile.iloc[poc_idx]

    low_idx = poc_idx
    high_idx = poc_idx
    n = len(profile)

    while accumulated < target_volume and (low_idx > 0 or high_idx < n - 1):
        vol_below = profile.iloc[low_idx - 1] if low_idx > 0 else -1
        vol_above = profile.iloc[high_idx + 1] if high_idx < n - 1 else -1

        # Standard rule: expand toward whichever side has more volume
        if vol_above >= vol_below:
            high_idx += 1
            accumulated += profile.iloc[high_idx]
        else:
            low_idx -= 1
            accumulated += profile.iloc[low_idx]

    vah_price = profile.index[high_idx]
    val_price = profile.index[low_idx]

    return VolumeProfileResult(
        poc=float(poc_price),
        vah=float(vah_price),
        val=float(val_price),
        profile=profile,
        total_volume=float(total_volume),
        value_area_volume=float(accumulated),
    )


def compute_session_profiles(
    ticks: pd.DataFrame,
    session_col: str = "session_date",
    price_col: str = "price",
    volume_col: str = "volume",
    tick_size: float = 0.05,
    value_area_pct: float = 0.70,
) -> dict:
    """
    Compute a separate volume profile per session (e.g., per trading day).

    Returns
    -------
    dict[session_key] -> VolumeProfileResult
    """
    results = {}
    for session_key, session_df in ticks.groupby(session_col):
        try:
            results[session_key] = compute_volume_profile(
                session_df,
                price_col=price_col,
                volume_col=volume_col,
                tick_size=tick_size,
                value_area_pct=value_area_pct,
            )
        except ValueError:
            continue
    return results


if __name__ == "__main__":
    # --- Example / smoke test with synthetic tick data ---
    np.random.seed(42)
    n_ticks = 20000

    # Simulate a price series that clusters around 18500-18550 (like Nifty Fut)
    prices = np.random.normal(loc=18525, scale=15, size=n_ticks).round(2)
    volumes = np.random.randint(1, 50, size=n_ticks)

    tick_df = pd.DataFrame({"price": prices, "volume": volumes})

    result = compute_volume_profile(tick_df, tick_size=1.0, value_area_pct=0.70)

    print(f"POC : {result.poc}")
    print(f"VAH : {result.vah}")
    print(f"VAL : {result.val}")
    print(f"Total Volume     : {result.total_volume:,.0f}")
    print(f"Value Area Volume: {result.value_area_volume:,.0f} "
          f"({result.value_area_volume/result.total_volume:.1%})")
