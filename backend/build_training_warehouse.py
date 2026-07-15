"""
build_training_warehouse.py
────────────────────────────
Turns accumulated OI_History (Excel sheet export, or the new json-mode
oi_history_log.parquet) into a labeled training set for virtual OI
estimation.

Target definition (real, checkable — no fabricated labels):
    At snapshot t, predict CE_OI_Delta / PE_OI_Delta *at snapshot t+1*,
    using only features observable at t. This is a genuine one-step-
    ahead forecast: once t+1 actually arrives in the log, its real
    CE_OI_Delta/PE_OI_Delta becomes the label for t's feature row.

    This means a strike's row is only trainable once two consecutive
    snapshots exist for it — single isolated runs produce zero usable
    rows, by design. The warehouse will grow as more runs accumulate.

Features (all real columns from oi_analysis.py / engine.py, nothing
fabricated):
    ce_vol_delta, pe_vol_delta   — CE_Volume_Delta / PE_Volume_Delta at t
    ce_oi_delta_lag, pe_oi_delta_lag  — this snapshot's own OI delta (t),
                                         used to predict the *next* one (t+1)
    ce_iv_delta, pe_iv_delta     — CE_IV_Delta / PE_IV_Delta at t
    minutes_since_last           — gap between t-1 and t (real cadence,
                                    not assumed fixed-interval)
    dist_from_atm                — abs(strike - spot) at t
    india_vix, iv_rank, hv30     — from ctx_dict, if available at t

Two independent targets are produced — ce_oi_delta_next and
pe_oi_delta_next — train separately or jointly, your call once features
exist in enough volume to look at.

Run this manually (or via cron) once enough history has accumulated.
It does NOT train anything itself — that's evaluate_and_deploy_pipeline's
job, once it's rewritten against this real schema (separate step, only
worth doing after inspecting how much usable data actually comes out
of this first).
"""

import os
import sys
import pandas as pd
import numpy as np

# Reuse the same log path oi_analysis.py writes to in --json mode
from oi_analysis import JSON_HISTORY_LOG_PATH

# FII/DII sentiment columns are logged alongside the existing feature set
# so they accumulate in the warehouse now, ready for a future retrain once
# enough trading days exist. They are NOT added to FEATURES in
# virtual_oi_estimator.py yet — the currently deployed models are unaffected.
try:
    from fii_dii_sentiment import get_feature_for_trading_day
except ImportError:
    get_feature_for_trading_day = None

_SENTIMENT_CACHE: dict = {}


def _sentiment_for_snapshot(snapshot_time) -> dict:
    """Memoized per-calendar-day lookup, so N rows on the same trading day
    only trigger one parquet read instead of N."""
    if get_feature_for_trading_day is None:
        return {}
    day_key = pd.Timestamp(snapshot_time).date()
    if day_key not in _SENTIMENT_CACHE:
        try:
            feats = get_feature_for_trading_day(pd.Timestamp(snapshot_time).to_pydatetime())
        except Exception as e:
            print(f"[Warehouse] FII/DII sentiment lookup failed for {day_key}: {e}")
            feats = None
        # Drop metadata keys (date/source_date/applies_to_trading_date) —
        # only numeric feature columns belong in the training table.
        # Prefix with "sent_" so these are clearly distinct from the
        # per-strike tick features when the warehouse schema is inspected.
        if feats:
            _SENTIMENT_CACHE[day_key] = {
                f"sent_{k}": v for k, v in feats.items()
                if k not in ("date", "source_date", "applies_to_trading_date")
            }
        else:
            _SENTIMENT_CACHE[day_key] = {}
    return _SENTIMENT_CACHE[day_key]


def load_excel_history(xlsx_export_path: str) -> pd.DataFrame:
    """
    Excel's OI_History sheet has to be exported to a file readable here
    (this script has no xlwings/COM dependency — keep it portable).
    Simplest path: from option_chain.py's Excel run, export the
    OI_History sheet to CSV/parquet once, point this at that file.
    """
    if not os.path.exists(xlsx_export_path):
        return pd.DataFrame()
    if xlsx_export_path.endswith(".parquet"):
        return pd.read_parquet(xlsx_export_path)
    return pd.read_csv(xlsx_export_path)


def load_json_mode_history() -> pd.DataFrame:
    if not os.path.exists(JSON_HISTORY_LOG_PATH):
        return pd.DataFrame()
    return pd.read_parquet(JSON_HISTORY_LOG_PATH)


def build_warehouse(
    excel_export_path: str = None,
    output_path: str = "quant_warehouse/training_rows.parquet",
) -> pd.DataFrame:
    """
    Combines Excel-mode and json-mode history (whichever exist), builds
    labeled (feature, target) rows, writes them to output_path.

    Returns the resulting DataFrame (empty if nothing usable yet).
    """
    frames = []

    json_hist = load_json_mode_history()
    if not json_hist.empty:
        frames.append(json_hist)
        print(f"[Warehouse] Loaded {len(json_hist)} rows from json-mode log.")

    if excel_export_path:
        excel_hist = load_excel_history(excel_export_path)
        if not excel_hist.empty:
            frames.append(excel_hist)
            print(f"[Warehouse] Loaded {len(excel_hist)} rows from Excel export.")

    if not frames:
        print("[Warehouse] No history found in either source. Nothing to build yet.")
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["snapshot_time"] = pd.to_datetime(df["snapshot_time"])
    df = df.drop_duplicates(subset=["Symbol", "StrikePrice", "Expiry", "snapshot_time"])
    df = df.sort_values(["Symbol", "StrikePrice", "Expiry", "snapshot_time"])

    # ── Build (t, t+1) pairs per strike/expiry/symbol ──────────────────
    rows = []
    group_cols = ["Symbol", "StrikePrice", "Expiry"]
    for _, g in df.groupby(group_cols):
        g = g.sort_values("snapshot_time").reset_index(drop=True)
        if len(g) < 2:
            continue  # need at least 2 snapshots to form one (t, t+1) pair

        for i in range(len(g) - 1):
            t_row    = g.iloc[i]
            t1_row   = g.iloc[i + 1]

            minutes_gap = (t1_row["snapshot_time"] - t_row["snapshot_time"]).total_seconds() / 60.0
            # Skip pairs with absurd gaps (e.g. across days, market closed
            # overnight) — these aren't representative of intracycle dynamics.
            if minutes_gap <= 0 or minutes_gap > 120:
                continue

            row = {
                "symbol":            t_row["Symbol"],
                "strike":            t_row["StrikePrice"],
                "expiry":            t_row["Expiry"],
                "snapshot_time":     t_row["snapshot_time"],
                "minutes_since_last": round(minutes_gap, 2),

                # Features observable at t
                "ce_vol_delta":      t_row.get("CE_Volume_Delta", 0) or 0,
                "pe_vol_delta":      t_row.get("PE_Volume_Delta", 0) or 0,
                "ce_oi_delta_lag":   t_row.get("CE_OI_Delta", 0) or 0,
                "pe_oi_delta_lag":   t_row.get("PE_OI_Delta", 0) or 0,
                "ce_iv_delta":       t_row.get("CE_IV_Delta", 0) or 0,
                "pe_iv_delta":       t_row.get("PE_IV_Delta", 0) or 0,

                # Targets: the REAL next-snapshot delta (ground truth, not fabricated)
                "ce_oi_delta_next":  t1_row.get("CE_OI_Delta", 0) or 0,
                "pe_oi_delta_next":  t1_row.get("PE_OI_Delta", 0) or 0,
            }
            # Logged for future use only — not yet part of FEATURES in
            # virtual_oi_estimator.py, so this has zero effect on the
            # currently deployed models. See module docstring at top.
            row.update(_sentiment_for_snapshot(t_row["snapshot_time"]))
            rows.append(row)

    warehouse = pd.DataFrame(rows)

    if warehouse.empty:
        print("[Warehouse] History exists but no valid (t, t+1) pairs were found "
              "(need ≥2 same-day snapshots per strike within a 120-min gap).")
        return warehouse

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    warehouse.to_parquet(output_path, index=False)
    print(f"[Warehouse] Built {len(warehouse)} labeled training rows → {output_path}")
    print(f"[Warehouse] Date range: {warehouse['snapshot_time'].min()} → {warehouse['snapshot_time'].max()}")
    print(f"[Warehouse] Unique strikes covered: {warehouse['strike'].nunique()}")

    return warehouse


if __name__ == "__main__":
    excel_path = sys.argv[1] if len(sys.argv) > 1 else None
    build_warehouse(excel_export_path=excel_path)
