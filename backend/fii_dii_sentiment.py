"""
fii_dii_sentiment.py
─────────────────────
Turns the raw NSE participant-wise OI files (fetched daily by
nse_eod_fetch.py → data/eod/fao_participant_oi/*.parquet) into a
day-over-day FII/DII/Pro/Retail comparison report.

Pro (proprietary/prop-desk trading) is included alongside FII/DII: unlike
FII/DII flow — which is frequently index-hedging or allocation-driven —
Pro desks run active, views-based option-writing books, so their
day-over-day OI shift is a genuinely distinct signal, not just a third
data point. Retail (NSE's "Client" row) is included too, mainly as the
contrarian-read counterpart to Pro/FII positioning.

This is the module mTerminals_json.py and build_training_warehouse.py
already import from (`from fii_dii_sentiment import
get_feature_for_trading_day`) but which never existed on disk — both
callers were silently no-op'ing via their `except ImportError` guards.

Design constraints inherited from the existing pipeline (see
mTerminals_json.py's comment block above _get_cached_fii_dii_sentiment):
    - Lagged, never same-day. NSE publishes fao_participant_oi_DDMMYYYY.csv
      for trading day D only after that day's close. So a session running
      ON day D can only ever see the file for D-1 (or earlier, if D-1's
      file is delayed/missing). We deliberately look strictly *before*
      the date passed in, to avoid lookahead.
    - Pure display/report feature for now — NOT part of virtual_oi_estimator's
      FEATURES whitelist, so nothing here touches the deployed OI models.
    - Cheap: one calendar-day cache is handled by the caller
      (mTerminals_json._FII_DII_CACHE); this module itself does a light
      in-process cache of the raw participant-OI DataFrames it reads,
      since the same two dates get re-requested every 5s poll tick.

Public API
──────────
    get_feature_for_trading_day(dt) -> dict
        dt: a date/datetime for the *current* trading session (usually
            "now"). Returns a flat dict:
                date                    -> dt's date, ISO string (metadata)
                source_date             -> the EOD file actually used, ISO string (metadata)
                compare_date            -> the EOD file it was compared against, ISO string (metadata)
                applies_to_trading_date -> same as `date`, kept for callers
                                            that filter out the other two
                                            metadata keys by name (metadata)
                ... numeric sentiment / comparison features (see below) ...
            Returns {} if no usable EOD file exists yet (e.g. before the
            first EOD fetch has ever run).

    get_report_for_trading_day(dt) -> dict
        Richer version for a dedicated "FII/DII OI Report" UI panel:
        the raw previous-day breakup table (FII vs DII vs Pro vs Client,
        every NSE column) PLUS the same comparison block as above,
        structured for direct rendering rather than as flat ML features.

Feature naming
──────────────
Every non-metadata key is prefixed by participant (`fii_` / `dii_`) so
build_training_warehouse.py's `sent_` prefixing stays unambiguous:
`sent_fii_index_fut_net_chg`, `sent_dii_opt_pcr`, etc.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from nse_eod_fetch import DATA_DIR, is_trading_day

_DATASET = "fao_participant_oi"

# ── Small in-process cache: (date_str -> DataFrame|None) ───────────────
# Avoids re-reading parquet off disk on every 5s poll tick for dates
# that have already been resolved this process lifetime.
_FILE_CACHE: dict = {}

# NSE's participant-type label varies slightly release to release
# ("FII", "FPI", "FII/FPI") — match by substring, case-insensitive,
# rather than an exact string.
_PARTICIPANT_ALIASES = {
    "fii": ("fii", "fpi"),
    "dii": ("dii",),
    # "Pro" = NSE's label for proprietary/prop-desk trading — these are the
    # heaviest option WRITERS of the four participant categories (FII/DII
    # flows are often index-hedging or allocation-driven; Pro desks run
    # active, views-based writing books), so their day-over-day OI shift is
    # a genuinely different signal from FII/DII, not just a third data point.
    "pro": ("pro", "proprietary"),
    # "Client" = NSE's label for retail/individual traders.
    "retail": ("client",),
}

# Columns as normalized by nse_eod_fetch.normalize_participant_df()
# (lowercased, spaces/dashes -> underscores). Not every NSE release ships
# every column, so all lookups below go through _num() with a 0 default.
_COLS = [
    "future_index_long", "future_index_short",
    "future_stock_long", "future_stock_short",
    "option_index_call_long", "option_index_put_long",
    "option_index_call_short", "option_index_put_short",
    "option_stock_call_long", "option_stock_put_long",
    "option_stock_call_short", "option_stock_put_short",
    "total_long_contracts", "total_short_contracts",
]


def _num(row: pd.Series, col: str) -> float:
    if row is None or col not in row.index:
        return 0.0
    val = row[col]
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _file_path_for(d: date) -> str:
    return os.path.join(DATA_DIR, _DATASET, f"{_DATASET}_{d.strftime('%Y%m%d')}.parquet")


def _load_participant_oi(d: date) -> Optional[pd.DataFrame]:
    """Read the normalized participant-OI parquet for calendar date `d`,
    or None if it doesn't exist (not fetched yet / non-trading day / gap)."""
    key = d.isoformat()
    if key in _FILE_CACHE:
        return _FILE_CACHE[key]

    path = _file_path_for(d)
    df = None
    if os.path.exists(path):
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            print(f"[fii_dii_sentiment] Failed reading {path}: {e}")
            df = None

    _FILE_CACHE[key] = df
    return df


def _find_latest_before(target: date, max_lookback_days: int = 10) -> Optional[date]:
    """Most recent calendar date strictly before `target` that has a
    participant-OI file on disk. Walks backwards day by day (skipping
    obvious non-trading days) rather than assuming a fixed T-1 offset,
    since holidays/weekends/late publications all shift the real answer."""
    cursor = target - timedelta(days=1)
    checked = 0
    while checked < max_lookback_days:
        if is_trading_day(datetime(cursor.year, cursor.month, cursor.day)):
            if _load_participant_oi(cursor) is not None:
                return cursor
        cursor -= timedelta(days=1)
        checked += 1
    return None


def _row_for(df: pd.DataFrame, participant: str) -> Optional[pd.Series]:
    """First row whose client_type matches the participant alias set."""
    if df is None or df.empty or "client_type" not in df.columns:
        return None
    aliases = _PARTICIPANT_ALIASES[participant]
    mask = df["client_type"].astype(str).str.lower().str.strip().apply(
        lambda s: any(a in s for a in aliases)
    )
    matches = df[mask]
    if matches.empty:
        return None
    return matches.iloc[0]


def _safe_ratio(numer: float, denom: float) -> float:
    return round(numer / denom, 3) if denom else 0.0


def _participant_block(row: Optional[pd.Series]) -> dict:
    """Raw positional figures for one participant on one day — used both
    standalone (report table) and as the basis for delta/ratio features."""
    if row is None:
        return {c: 0.0 for c in _COLS}
    return {c: _num(row, c) for c in _COLS}


def _derived_metrics(block: dict) -> dict:
    """Ratios/nets computed off one day's raw block — same shape for both
    'current' and 'compare' days so deltas are a straight subtraction."""
    idx_fut_net = block["future_index_long"] - block["future_index_short"]
    stk_fut_net = block["future_stock_long"] - block["future_stock_short"]
    opt_call_net = block["option_index_call_long"] - block["option_index_call_short"]
    opt_put_net = block["option_index_put_long"] - block["option_index_put_short"]
    total_net = block["total_long_contracts"] - block["total_short_contracts"]

    return {
        "index_fut_long": block["future_index_long"],
        "index_fut_short": block["future_index_short"],
        "index_fut_net": idx_fut_net,
        "index_fut_long_short_ratio": _safe_ratio(
            block["future_index_long"], block["future_index_short"]
        ),
        "stock_fut_net": stk_fut_net,
        "opt_index_call_net": opt_call_net,
        "opt_index_put_net": opt_put_net,
        # Put/Call OI ratio on the index-option book — a classic
        # sentiment gauge: >1 skews defensive/bearish, <1 skews bullish.
        "opt_index_pcr": _safe_ratio(
            block["option_index_put_long"] + block["option_index_put_short"],
            block["option_index_call_long"] + block["option_index_call_short"],
        ),
        "total_net": total_net,
    }


def _classify_sentiment(idx_fut_net_chg: float, opt_pcr_chg: float) -> str:
    """Coarse day-over-day tag from index-future net-OI change plus PCR
    drift. Thresholds are in contracts, not %, since NSE participant OI
    is already an absolute (not notional) figure — tune against your own
    data once a few weeks of history accumulate."""
    if idx_fut_net_chg > 5000 and opt_pcr_chg >= 0:
        return "Bullish Build-up"
    if idx_fut_net_chg < -5000 and opt_pcr_chg <= 0:
        return "Bearish Build-up"
    if abs(idx_fut_net_chg) <= 5000:
        return "Neutral"
    return "Mixed"


def _compare(curr_block: dict, prev_block: Optional[dict]) -> dict:
    """current - previous for every derived metric, prefixed `_chg`."""
    curr = _derived_metrics(curr_block)
    prev = _derived_metrics(prev_block) if prev_block is not None else {k: 0.0 for k in curr}
    out = {}
    for k, v in curr.items():
        out[k] = v
        out[f"{k}_chg"] = round(v - prev.get(k, 0.0), 2)
    return out


def get_feature_for_trading_day(dt) -> dict:
    """Flat feature dict for the given session date `dt`. See module
    docstring for the exact contract. Returns {} if nothing usable yet."""
    target = dt.date() if isinstance(dt, datetime) else dt

    source_date = _find_latest_before(target)
    if source_date is None:
        return {}

    compare_date = _find_latest_before(source_date)

    curr_df = _load_participant_oi(source_date)
    prev_df = _load_participant_oi(compare_date) if compare_date else None

    out = {
        "date": target.isoformat(),
        "source_date": source_date.isoformat(),
        "compare_date": compare_date.isoformat() if compare_date else None,
        "applies_to_trading_date": target.isoformat(),
    }

    for participant in ("fii", "dii", "pro", "retail"):
        curr_row = _row_for(curr_df, participant)
        prev_row = _row_for(prev_df, participant) if prev_df is not None else None

        curr_block = _participant_block(curr_row)
        prev_block = _participant_block(prev_row) if prev_row is not None else None

        comp = _compare(curr_block, prev_block)
        for k, v in comp.items():
            out[f"{participant}_{k}"] = v

        out[f"{participant}_sentiment"] = _classify_sentiment(
            comp["index_fut_net_chg"], comp["opt_index_pcr_chg"]
        )

    # Cross-participant divergence: FII and DII index-future positioning
    # moving in opposite directions is a commonly-watched signal.
    fii_dir = out["fii_index_fut_net_chg"]
    dii_dir = out["dii_index_fut_net_chg"]
    out["fii_dii_divergence"] = bool(
        (fii_dir > 0 and dii_dir < 0) or (fii_dir < 0 and dii_dir > 0)
    )
    # Pro desks are the primary WRITERS among the four participant types, so
    # Pro moving opposite to FII+DII combined flow is worth flagging
    # separately from the existing FII/DII divergence check above.
    pro_dir = out["pro_index_fut_net_chg"]
    fii_dii_combined_dir = fii_dir + dii_dir
    out["pro_vs_fii_dii_divergence"] = bool(
        (pro_dir > 0 and fii_dii_combined_dir < 0) or (pro_dir < 0 and fii_dii_combined_dir > 0)
    )

    return out


def get_report_for_trading_day(dt) -> dict:
    """Richer structure for a dedicated FII/DII/Pro OI report panel: raw
    previous-day breakup (every NSE column, all three participants, both
    comparison days) plus the same comparison/sentiment block as
    get_feature_for_trading_day(). Shape is display-oriented, not a
    flat ML feature row."""
    target = dt.date() if isinstance(dt, datetime) else dt

    source_date = _find_latest_before(target)
    if source_date is None:
        return {"available": False}

    compare_date = _find_latest_before(source_date)
    curr_df = _load_participant_oi(source_date)
    prev_df = _load_participant_oi(compare_date) if compare_date else None

    report = {
        "available": True,
        "asOf": target.isoformat(),
        "sourceDate": source_date.isoformat(),
        "compareDate": compare_date.isoformat() if compare_date else None,
        "participants": {},
    }

    for participant in ("fii", "dii", "pro", "retail"):
        curr_row = _row_for(curr_df, participant)
        prev_row = _row_for(prev_df, participant) if prev_df is not None else None

        curr_block = _participant_block(curr_row)
        prev_block = _participant_block(prev_row) if prev_row is not None else None

        comp = _compare(curr_block, prev_block)

        report["participants"][participant] = {
            "raw": curr_block,
            "rawPrevious": prev_block or {c: 0.0 for c in _COLS},
            "derived": comp,
            "sentiment": _classify_sentiment(
                comp["index_fut_net_chg"], comp["opt_index_pcr_chg"]
            ),
        }

    fii_dir = report["participants"]["fii"]["derived"]["index_fut_net_chg"]
    dii_dir = report["participants"]["dii"]["derived"]["index_fut_net_chg"]
    report["divergence"] = bool(
        (fii_dir > 0 and dii_dir < 0) or (fii_dir < 0 and dii_dir > 0)
    )
    pro_dir = report["participants"]["pro"]["derived"]["index_fut_net_chg"]
    report["proDivergence"] = bool(
        (pro_dir > 0 and (fii_dir + dii_dir) < 0) or (pro_dir < 0 and (fii_dir + dii_dir) > 0)
    )

    return report


if __name__ == "__main__":
    import json
    today = datetime.now()
    print("── get_feature_for_trading_day ──")
    print(json.dumps(get_feature_for_trading_day(today), indent=2))
    print("── get_report_for_trading_day ──")
    print(json.dumps(get_report_for_trading_day(today), indent=2))
