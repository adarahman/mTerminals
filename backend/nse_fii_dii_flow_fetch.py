"""
nse_fii_dii_flow_fetch.py

Cash-market FII/DII net flow (Rs. Cr) — fetch + local history store.

This is intentionally separate from fao_participant_oi / fii_dii_sentiment:
those cover F&O participant-wise Open Interest. This module covers the
much simpler daily cash-market buy/sell/net figures NSE publishes for
FII/FPI and DII, e.g. via https://www.nseindia.com/api/fiidiiTradeReact

Public contract (used by ws_server_live.py):
    record_today_flow() -> bool
        Blocking. Fetches the latest published FII/DII cash flow row from
        NSE and appends it to the local CSV history if it's a new date.
        Returns True if a new row was recorded, False if there was
        nothing new to record (e.g. NSE hasn't published today's/latest
        figures yet). Raises on genuine fetch/parse errors so the caller's
        done-callback can log them.

    get_flow_series(n: int = 30) -> dict
        Blocking, no network call. Reads the local CSV history and returns
        the last `n` trading days as:
            {
                "dates": ["18-Jul-2026", "17-Jul-2026", ...],  # oldest->newest
                "fii":   [1234.5, -321.0, ...],                # Rs. Cr, net
                "dii":   [-88.2, 410.6, ...],                  # Rs. Cr, net
            }
        Returns {"dates": [], "fii": [], "dii": []} if no history exists yet.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(_HERE, "nse_fii_dii_flow_history.csv")

NSE_HOME_URL = "https://www.nseindia.com"
NSE_FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
}

_CSV_FIELDS = ["date", "fii_net_cr", "dii_net_cr"]


# ---------------------------------------------------------------------------
# NSE fetch
# ---------------------------------------------------------------------------

def _nse_session() -> requests.Session:
    """NSE's API 403s without cookies from a prior homepage hit."""
    s = requests.Session()
    s.headers.update(_HEADERS)
    # Priming hit — establishes cookies the API endpoint checks for.
    s.get(NSE_HOME_URL, timeout=10)
    return s


def _fetch_latest_fii_dii_row() -> Optional[dict]:
    """
    Hits NSE's fiidiiTradeReact endpoint and returns the most recent
    row as {"date": "18-Jul-2026", "fii_net_cr": float, "dii_net_cr": float},
    or None if the endpoint returned no usable data.

    NSE's response shape is a list of dicts like:
        {"category": "FII/FPI *", "date": "18-Jul-2026",
         "buyValue": "12345.67", "sellValue": "11234.56", "netValue": "1111.11"}
        {"category": "DII **", "date": "18-Jul-2026", ...}
    """
    session = _nse_session()
    resp = session.get(NSE_FII_DII_URL, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list) or not data:
        return None

    fii_net = None
    dii_net = None
    row_date = None

    for row in data:
        category = str(row.get("category", "")).upper()
        net_str = row.get("netValue")
        if net_str in (None, ""):
            continue
        try:
            net_val = float(net_str)
        except (TypeError, ValueError):
            continue

        if "FII" in category or "FPI" in category:
            fii_net = net_val
            row_date = row_date or row.get("date")
        elif "DII" in category:
            dii_net = net_val
            row_date = row_date or row.get("date")

    if row_date is None or fii_net is None or dii_net is None:
        return None

    return {"date": row_date, "fii_net_cr": fii_net, "dii_net_cr": dii_net}


# ---------------------------------------------------------------------------
# Local CSV history
# ---------------------------------------------------------------------------

def _read_history() -> list[dict]:
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, newline="") as f:
        return list(csv.DictReader(f))


def _write_history(rows: list[dict]) -> None:
    tmp_path = CSV_PATH + ".tmp"
    with open(tmp_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, CSV_PATH)  # atomic swap, avoids a torn file on crash


def _parse_nse_date(d: str) -> datetime:
    return datetime.strptime(d, "%d-%b-%Y")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_today_flow() -> bool:
    """
    Fetch the latest FII/DII cash-flow row from NSE and append it to the
    local history if it's a date we don't already have.

    Returns:
        True  -> a new row was fetched and recorded
        False -> fetch succeeded but there was nothing new (already have
                 that date, e.g. NSE hasn't rolled over to a fresh day's
                 figures yet)

    Raises whatever requests/json errors occur on genuine failure, so the
    caller's done-callback can log a traceback.
    """
    latest = _fetch_latest_fii_dii_row()
    if latest is None:
        return False

    rows = _read_history()
    existing_dates = {r["date"] for r in rows}

    if latest["date"] in existing_dates:
        return False

    rows.append({
        "date": latest["date"],
        "fii_net_cr": f"{latest['fii_net_cr']:.2f}",
        "dii_net_cr": f"{latest['dii_net_cr']:.2f}",
    })
    # Keep the file sorted oldest->newest by trading date.
    rows.sort(key=lambda r: _parse_nse_date(r["date"]))
    _write_history(rows)
    return True


def get_flow_series(n: int = 30) -> dict:
    """
    Read the last `n` trading days from local history. No network call.
    """
    rows = _read_history()
    rows.sort(key=lambda r: _parse_nse_date(r["date"]))
    recent = rows[-n:] if n else rows

    return {
        "dates": [r["date"] for r in recent],
        "fii": [float(r["fii_net_cr"]) for r in recent],
        "dii": [float(r["dii_net_cr"]) for r in recent],
    }


if __name__ == "__main__":
    # Manual smoke test: python nse_fii_dii_flow_fetch.py
    recorded = record_today_flow()
    print(f"record_today_flow() -> {recorded}")
    print(get_flow_series(10))
