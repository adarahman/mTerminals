"""
index_contributors.py
----------------------
Top drivers/draggers computation for an index's own basket: given the
already-fetched df_idx frame (one NSE HTTP call per tick, shared across
consumers), returns each constituent's free-float-weighted point impact
on the index, sorted by magnitude.

Moved from option_chain_json.py (Step 5c of the v4 migration plan). Pure
move + rename only: no behavioral changes, no signature changes.
"""

from __future__ import annotations

import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "SYMBOL_TO_INDEX_BASKET",
    "_compute_index_contributors",
]


# Maps our dashboard SYMBOL to the literal "Index" tag used inside df_idx
# (i.e. the exact string passed to fetch_fno_index() as part of
# DEFAULT_INDICES in market_api.py). Only NSE index-basket symbols have an
# entry — BSE symbols (SENSEX/BANKEX/SENSEX50/PNB) aren't in DEFAULT_INDICES,
# so contributors will legitimately be empty for those.
SYMBOL_TO_INDEX_BASKET = {
    "NIFTY":      "NIFTY 50",
    "BANKNIFTY":  "NIFTY BANK",
    "FINNIFTY":   "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
}


def _compute_index_contributors(df_idx, symbol, index_spot):
    """Top drivers/draggers for `symbol`'s own index basket, derived from
    df_idx (already fetched once per tick via fetch_all_indices() — no new
    network call here). Weight is approximated live from free-float market
    cap (ffmc_i / sum(ffmc)) when NSE actually returns that field; falls
    back to equal weighting across the basket if it doesn't (some NSE
    endpoint variants omit ffmc), so the widget still populates rather
    than silently staying empty. Prints a one-line reason whenever it
    returns [] or falls back, so this is diagnosable from the console.
    """
    basket = SYMBOL_TO_INDEX_BASKET.get(symbol)
    if not basket:
        logger.warning(f"[Contributors] Skip: no index basket mapped for SYMBOL='{symbol}' (expected for BSE symbols).")
        return []
    if df_idx is None or df_idx.empty or "Index" not in df_idx.columns:
        logger.warning(f"[Contributors] Skip: df_idx is empty or missing 'Index' column.")
        return []

    rows = df_idx[df_idx["Index"] == basket]
    if rows.empty:
        available = sorted(df_idx["Index"].unique().tolist())
        logger.warning(f"[Contributors] Skip: no rows tagged Index='{basket}' in df_idx. Available Index tags: {available}")
        return []

    ffmc_vals = rows["ffmc"] if "ffmc" in rows.columns else pd.Series(dtype=float)
    ffmc_numeric = pd.to_numeric(ffmc_vals, errors="coerce")
    valid_ffmc = ffmc_numeric.notna() & (ffmc_numeric > 0)
    total_ffmc = float(ffmc_numeric.loc[valid_ffmc].sum()) if valid_ffmc.any() else 0.0

    use_equal_weight = not total_ffmc
    if use_equal_weight:
        n = len(rows)
        logger.warning(f"[Contributors] WARNING: 'ffmc' missing/zero for all {n} rows in '{basket}' "
              f"(NSE didn't return it for this endpoint) — falling back to equal weighting "
              f"({round(100/n, 2)}% each). Point-impact ranking will be less accurate than "
              f"true free-float weight until this is investigated.")

    contributors = []
    n_rows = len(rows)
    for _, r in rows.iterrows():
        ffmc = float(r.get("ffmc") or 0)
        if use_equal_weight or (not math.isfinite(ffmc) or ffmc <= 0):
            weight = 100.0 / n_rows if n_rows else 0
        else:
            weight = (ffmc / total_ffmc) * 100
        pct_change = float(r.get("% Change") or 0)
        if not math.isfinite(pct_change):
            pct_change = 0.0
        point_impact = round((pct_change * weight * index_spot) / 10000, 2)
        if not math.isfinite(point_impact):
            point_impact = 0.0
        contributors.append({
            "symbol":       r.get("Symbol"),
            "weightage":    round(weight, 2),
            "ltp":          r.get("Last Price"),
            "change":       r.get("Change"),
            "pct_change":   pct_change,
            "point_impact": point_impact,
        })

    if not contributors:
        logger.warning(f"[Contributors] Skip: '{basket}' had {n_rows} row(s) but none produced a usable contributor entry.")

    contributors.sort(key=lambda c: abs(c["point_impact"]), reverse=True)
    return contributors

