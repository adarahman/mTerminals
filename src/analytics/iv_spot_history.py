"""
iv_spot_history.py

Per-symbol daily-close ATM IV + spot history — local store, no network
dependency of its own.

This exists because `oi/chain_metrics.py`'s `_compute_iv_rank_hv30()`
requires a multi-day time series to compute anything meaningful, but the
pipeline's own per-tick `history_df` (`oi_analysis.build_oi_history()`) is
rebuilt fresh every tick as a single-snapshot diff against the immediately
prior tick — it is never accumulated across ticks or days, so IV Rank/HV30
silently fell back to hardcoded stubs on effectively every call. See
PDS-09_Volatility.md's "Backend readiness" section for the full writeup.

Same read/write/dedup-by-date pattern as
`analytics/nse_fii_dii_flow_fetch.py` — one row per (symbol, trading day),
atomic file swap, sorted oldest->newest. Deliberately separate from that
module (different underlying, different fields) rather than generalized
into one shared history file, matching this project's existing preference
for one file per distinct daily-history concern.

Public contract (used by ws_server_live.py):
    record_today_iv_spot(symbol, atm_iv, spot) -> bool
        No network call — the caller already has today's live atm_iv/spot
        from the current pipeline tick. Appends a row to the local CSV if
        today's date isn't already recorded for this symbol. Intended to
        be called once per trading day, near market close, from the same
        EOD-trigger block that calls record_today_flow() — so the
        recorded value approximates the day's close rather than an
        arbitrary intraday tick. Returns True if a new row was recorded,
        False if today was already recorded (e.g. called twice, or the
        EOD guard elsewhere let it through more than once).

    get_iv_spot_series(symbol, n=252) -> dict
        Blocking, no network call. Reads the local CSV history for this
        symbol and returns the last `n` trading days as:
            {
                "dates":  ["2026-07-18", "2026-07-21", ...],  # oldest->newest
                "atm_iv": [14.2, 13.8, ...],                   # percent
                "spot":   [24850.6, 24910.3, ...],
            }
        Returns {"dates": [], "atm_iv": [], "spot": []} if no history
        exists yet for this symbol.
"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime

from paths import CACHE_DIR

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CSV_FIELDS = ["date", "atm_iv", "spot"]


def _csv_path(symbol: str) -> str:
    # One file per symbol — IV/spot baselines differ wildly across
    # underlyings (NIFTY vs a single stock), so a shared file would mean
    # every reader has to filter by symbol anyway. Matches this project's
    # existing per-symbol instrument-cache file-per-underlying pattern.
    safe_symbol = "".join(c for c in symbol.upper() if c.isalnum()) or "UNKNOWN"
    return os.path.join(CACHE_DIR, f"iv_spot_history_{safe_symbol}.csv")


# ---------------------------------------------------------------------------
# Local CSV history
# ---------------------------------------------------------------------------

def _read_history(symbol: str) -> list[dict]:
    path = _csv_path(symbol)
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _write_history(symbol: str, rows: list[dict]) -> None:
    path = _csv_path(symbol)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, path)  # atomic swap, avoids a torn file on crash


def _parse_iso_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_today_iv_spot(symbol: str, atm_iv: float, spot: float) -> bool:
    """
    Record today's (symbol, atm_iv, spot) as today's closing-approximation
    row, if today isn't already recorded for this symbol.

    No network call — atm_iv/spot are supplied by the caller from the
    current live pipeline tick. Call once per trading day, near market
    close, so the recorded value approximates the close rather than an
    arbitrary intraday reading.

    Returns:
        True  -> a new row was recorded
        False -> today was already recorded for this symbol (no-op)
    """
    if atm_iv is None or spot is None:
        return False
    try:
        atm_iv = float(atm_iv)
        spot = float(spot)
    except (TypeError, ValueError):
        return False
    if atm_iv <= 0 or spot <= 0:
        return False

    today_str = date.today().isoformat()

    rows = _read_history(symbol)
    existing_dates = {r["date"] for r in rows}
    if today_str in existing_dates:
        return False

    rows.append({
        "date": today_str,
        "atm_iv": f"{atm_iv:.4f}",
        "spot": f"{spot:.4f}",
    })
    # Keep the file sorted oldest->newest by trading date.
    rows.sort(key=lambda r: _parse_iso_date(r["date"]))
    _write_history(symbol, rows)
    return True


def get_iv_spot_series(symbol: str, n: int = 252) -> dict:
    """
    Read the last `n` trading days of ATM IV + spot history for this
    symbol. No network call.
    """
    rows = _read_history(symbol)
    rows.sort(key=lambda r: _parse_iso_date(r["date"]))
    recent = rows[-n:] if n else rows

    return {
        "dates":  [r["date"] for r in recent],
        "atm_iv": [float(r["atm_iv"]) for r in recent],
        "spot":   [float(r["spot"]) for r in recent],
    }


if __name__ == "__main__":
    # Manual smoke test: python iv_spot_history.py
    recorded = record_today_iv_spot("NIFTY", 14.2, 24850.6)
    print(f"record_today_iv_spot('NIFTY', ...) -> {recorded}")
    print(get_iv_spot_series("NIFTY", 10))
