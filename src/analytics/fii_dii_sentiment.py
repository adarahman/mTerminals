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
    - Pure display/report feature for now — NOT part of ml.inference's
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

Sentiment scoring
─────────────────
`{participant}_sentiment` is still one of "Bullish Build-up" /
"Bearish Build-up" / "Neutral" / "Mixed" (unchanged shape for existing
callers), but it's now derived from a weighted composite score across six
signals (index futures, stock futures, index call OI, index put OI, total
net contracts, PCR drift) rather than just index-future net change + PCR.
Each per-participant change is normalized against that participant's own
open-interest footprint before weighting, so a big absolute OI shift from
a small book doesn't get treated the same as an equally-sized shift from
a large one. Two new fields ride along with each `_sentiment`:
    {participant}_score      -> -100..100, signed composite
    {participant}_confidence -> 0..100, blend of score magnitude and
                                 cross-signal agreement
`retail_contrarian_read` gives the inverted (fade-the-crowd) reading of
retail's own sentiment, since retail flow is generally more useful as a
contrarian indicator than as a directional one.

Classification thresholds (`BULLISH_SCORE_THRESHOLD`,
`NEUTRAL_SCORE_THRESHOLD`) and signal weights (`_SCORE_WEIGHTS`) are
module-level constants — override via env vars or by editing the
constants directly rather than hunting for magic numbers in the
classifier body.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from nse_eod_fetch import DATA_DIR, is_trading_day
from storage.caches import MemoCache

_DATASET = "fao_participant_oi"

# ── Classification thresholds ───────────────────────────────────────────
# Previously hard-coded (`if idx_fut_net_chg > 5000`) directly in the
# classifier body. Now applied to the normalized -100..100 composite score
# (see _composite_score below) rather than raw contract counts, so these
# don't need retuning every time participation volumes drift — but they're
# still just eyeballed defaults, not statistically derived. Override via
# env var if a participant/index combo needs different sensitivity.
BULLISH_SCORE_THRESHOLD = float(os.environ.get("FII_DII_BULLISH_SCORE_THRESHOLD", 20.0))
NEUTRAL_SCORE_THRESHOLD = float(os.environ.get("FII_DII_NEUTRAL_SCORE_THRESHOLD", 8.0))

# ── Composite score signal weights ──────────────────────────────────────
# Each key is a `_chg` field from _compare()'s output. Sign convention:
# positive weight = rising value reads bullish; negative weight = rising
# value reads bearish (e.g. put OI build-up or PCR rising are defensive).
# Weights don't need to sum to 1 — _composite_score normalizes by total
# absolute weight when scaling to -100..100.
_SCORE_WEIGHTS = {
    "index_fut_net_chg": 0.35,
    "stock_fut_net_chg": 0.15,
    "opt_index_call_net_chg": 0.15,
    "opt_index_put_net_chg": -0.15,
    "total_net_chg": 0.10,
    "opt_index_pcr_chg": -0.10,
}

# ── Small in-process cache: (date_str -> DataFrame|None) ───────────────
# Avoids re-reading parquet off disk on every 5s poll tick for dates
# that have already been resolved this process lifetime.
_FILE_CACHE = MemoCache()

# NSE's participant-type label varies slightly release to release
# ("FII", "FPI", "FII/FPI") — match by whole token, case-insensitive,
# after splitting on non-alphanumeric characters. Token matching (rather
# than plain substring) avoids a future NSE label that happens to contain
# one of these fragments (e.g. something like "Approx" would previously
# have false-matched "pro") from silently misclassifying a row.
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
        return _FILE_CACHE.get(key)

    path = _file_path_for(d)
    df = None
    if os.path.exists(path):
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            print(f"[fii_dii_sentiment] Failed reading {path}: {e}")
            df = None

    _FILE_CACHE.set(key, df)
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


_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _label_tokens(label: str) -> set[str]:
    """'FII/FPI *' -> {'fii', 'fpi'}; 'Client' -> {'client'}. Splitting on
    non-alphanumerics and matching whole tokens (rather than `a in s`
    substring checks) means a fragment like "pro" only matches an actual
    "Pro"/"Proprietary" token, not an incidental substring inside some
    future NSE label."""
    return set(_TOKEN_SPLIT_RE.split(label.lower().strip())) - {""}


def _row_for(df: pd.DataFrame, participant: str) -> Optional[pd.Series]:
    """First row whose client_type token-matches the participant alias set."""
    if df is None or df.empty or "client_type" not in df.columns:
        return None
    aliases = set(_PARTICIPANT_ALIASES[participant])
    mask = df["client_type"].astype(str).apply(
        lambda s: bool(_label_tokens(s) & aliases)
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


def _compute_divergence(dir_a: float, dir_b: float) -> bool:
    """True when two directional figures have strictly opposite signs.
    Shared by the FII-vs-DII and Pro-vs-(FII+DII) checks in both
    get_feature_for_trading_day() and get_report_for_trading_day() —
    previously this exact `(a > 0 and b < 0) or (a < 0 and b > 0)` logic
    was duplicated four times across those two functions."""
    return bool((dir_a > 0 and dir_b < 0) or (dir_a < 0 and dir_b > 0))


def _composite_score(comp: dict, curr_block: dict) -> dict:
    """Weighted, OI-normalized multi-signal sentiment score.

    Replaces the old two-input (index-future net chg + PCR chg) threshold
    check. Every configured `_chg` signal (see _SCORE_WEIGHTS) is folded
    in, and each contract-count-based signal is normalized against this
    participant's own total OI footprint (total_long + total_short
    contracts) before weighting — so a 5,000-contract swing from a
    50,000-contract book counts for more than the same 5,000-contract
    swing from a 5,000,000-contract book, addressing the "ratio without
    volume context" issue in the raw PCR check.

    Returns:
        {"score": -100..100, "label": one of the four sentiment tags,
         "confidence": 0..100}
    confidence blends score magnitude (how far from neutral) with signal
    agreement (what fraction of the weighted signals point the same
    direction as the overall score) — a large move where every input
    agrees scores higher confidence than the same magnitude produced by
    signals fighting each other.
    """
    denom = curr_block["total_long_contracts"] + curr_block["total_short_contracts"]
    denom = denom if denom > 0 else 1.0
    total_weight = sum(abs(w) for w in _SCORE_WEIGHTS.values())

    normalized: dict[str, float] = {}
    for key in _SCORE_WEIGHTS:
        raw_chg = comp.get(key, 0.0)
        if key == "opt_index_pcr_chg":
            # PCR is already a ratio (typically moves by hundredths, not
            # contract counts), so it gets its own small fixed scale
            # rather than being divided by open-interest volume.
            frac = raw_chg / 0.15
        else:
            frac = raw_chg / denom
        normalized[key] = max(-1.0, min(1.0, frac))

    weighted_sum = sum(normalized[k] * w for k, w in _SCORE_WEIGHTS.items())
    score = round(max(-100.0, min(100.0, 100 * weighted_sum / total_weight)), 1)

    agree_weight = sum(
        abs(w) for k, w in _SCORE_WEIGHTS.items()
        if normalized[k] != 0 and (normalized[k] * w) * score >= 0
    )
    consensus = agree_weight / total_weight if total_weight else 0.0
    confidence = round(
        max(0.0, min(100.0, 100 * (0.5 * abs(score) / 100 + 0.5 * consensus))), 1
    )

    if score >= BULLISH_SCORE_THRESHOLD:
        label = "Bullish Build-up"
    elif score <= -BULLISH_SCORE_THRESHOLD:
        label = "Bearish Build-up"
    elif abs(score) <= NEUTRAL_SCORE_THRESHOLD:
        label = "Neutral"
    else:
        label = "Mixed"

    return {"score": score, "label": label, "confidence": confidence}


# Fade-the-crowd reading of retail's own sentiment label. Retail flow is
# generally more informative as a contrarian signal than a directional
# one, so this rides alongside retail_sentiment rather than replacing it.
_CONTRARIAN_LABELS = {
    "Bullish Build-up": "Retail buying — contrarian caution (bearish tilt)",
    "Bearish Build-up": "Retail selling — contrarian opportunity (bullish tilt)",
    "Neutral": "No strong contrarian read",
    "Mixed": "Mixed signal — no clear contrarian read",
}


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

        scored = _composite_score(comp, curr_block)
        out[f"{participant}_sentiment"] = scored["label"]
        out[f"{participant}_score"] = scored["score"]
        out[f"{participant}_confidence"] = scored["confidence"]

    # Retail is more useful as a contrarian read than a directional one.
    out["retail_contrarian_read"] = _CONTRARIAN_LABELS[out["retail_sentiment"]]

    # Cross-participant divergence: FII and DII index-future positioning
    # moving in opposite directions is a commonly-watched signal.
    fii_dir = out["fii_index_fut_net_chg"]
    dii_dir = out["dii_index_fut_net_chg"]
    out["fii_dii_divergence"] = _compute_divergence(fii_dir, dii_dir)
    # Pro desks are the primary WRITERS among the four participant types, so
    # Pro moving opposite to FII+DII combined flow is worth flagging
    # separately from the existing FII/DII divergence check above.
    pro_dir = out["pro_index_fut_net_chg"]
    fii_dii_combined_dir = fii_dir + dii_dir
    out["pro_vs_fii_dii_divergence"] = _compute_divergence(pro_dir, fii_dii_combined_dir)

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
        scored = _composite_score(comp, curr_block)

        report["participants"][participant] = {
            "raw": curr_block,
            "rawPrevious": prev_block or {c: 0.0 for c in _COLS},
            "derived": comp,
            "sentiment": scored["label"],
            "score": scored["score"],
            "confidence": scored["confidence"],
        }

    report["participants"]["retail"]["contrarianRead"] = _CONTRARIAN_LABELS[
        report["participants"]["retail"]["sentiment"]
    ]

    fii_dir = report["participants"]["fii"]["derived"]["index_fut_net_chg"]
    dii_dir = report["participants"]["dii"]["derived"]["index_fut_net_chg"]
    report["divergence"] = _compute_divergence(fii_dir, dii_dir)
    pro_dir = report["participants"]["pro"]["derived"]["index_fut_net_chg"]
    report["proDivergence"] = _compute_divergence(pro_dir, fii_dir + dii_dir)

    return report


if __name__ == "__main__":
    import json
    today = datetime.now()
    print("── get_feature_for_trading_day ──")
    print(json.dumps(get_feature_for_trading_day(today), indent=2))
    print("── get_report_for_trading_day ──")
    print(json.dumps(get_report_for_trading_day(today), indent=2))