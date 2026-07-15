"""
nse_eod_fetch.py

Minimal, headless fetcher for two NSE end-of-day datasets used as
supplementary features / ground-truth checks for the VirtualOI pipeline:

    1. fao_participant_oi   - FII / DII / Pro / Client OI split (futures & options)
    2. fao_participant_vol  - Same split, but volume instead of OI
    3. combine_oi           - MWPL combined OI archive (zip), used to cross-check
                              our own live OI aggregation against NSE's own numbers

No Selenium, no GUI, no scheduler. Designed to be called once daily,
post market-close, from the existing engine.py scheduling loop.

If engine.py / mTerminals_json.py already maintains a live NSE requests
session (cookies kept warm from the live tick pipeline), pass that session
in via `session=` instead of letting this module create its own.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import datetime
from typing import Optional

import pandas as pd
import requests

from pathlib import Path

from pathlib import Path

logger = logging.getLogger(__name__)

# Where normalized EOD datasets get persisted. Override via
# nse_eod_fetch.DATA_DIR = Path(...) before calling fetch_all_eod(save=True),
# or just pass an explicit out_dir.
DATA_DIR = Path("data/eod")

# NSE trading holidays. This is NOT auto-updated - refresh yearly from
# NSE's published holiday calendar (https://www.nseindia.com/resources/exchange-communication-holidays).
# Only dates that matter for the current/next season need to be kept here.
NSE_HOLIDAYS_2026 = {
    "2026-01-26",  # Republic Day
    "2026-03-06",  # Holi
    "2026-04-03",  # Good Friday
    "2026-08-15",  # Independence Day
    "2026-10-02",  # Gandhi Jayanti
    "2026-11-09",  # Diwali (Laxmi Pujan) - verify exact date closer to the time
    "2026-12-25",  # Christmas
}


def is_trading_day(date_obj: Optional[datetime] = None) -> bool:
    """True if NSE is open on this date: not a weekend, not a known holiday.

    This is a best-effort check, not a source of truth - NSE occasionally
    announces ad-hoc closures. Update NSE_HOLIDAYS_2026 each year.
    """
    date_obj = date_obj or datetime.now()
    if date_obj.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    if date_obj.strftime("%Y-%m-%d") in NSE_HOLIDAYS_2026:
        return False
    return True

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    # Restrict to gzip/deflate to avoid Brotli-encoded CSV corruption
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

_URLS = {
    "fao_participant_oi": "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv",
    "fao_participant_vol": "https://nsearchives.nseindia.com/content/nsccl/fao_participant_vol_{ddmmyyyy}.csv",
    "combine_oi": "https://nsearchives.nseindia.com/archives/nsccl/mwpl/combineoi_{ddmmyyyy}.zip",
}


def _format_url(template: str, date_obj: datetime) -> str:
    return template.replace("{ddmmyyyy}", date_obj.strftime("%d%m%Y"))


def _new_session() -> requests.Session:
    """Create a fresh session and warm it up against the NSE homepage.

    Only use this if the caller doesn't already have a live NSE session
    (e.g. from the tick pipeline) to hand us. Warming a second session is
    wasted round-trips if one already exists.
    """
    session = requests.Session()
    session.headers.update(_BASE_HEADERS)
    try:
        session.get("https://www.nseindia.com", timeout=15)
        time.sleep(1)
    except requests.RequestException as exc:
        logger.warning("NSE homepage warm-up failed: %s", exc)
    return session


def _get_with_retry(
    session: requests.Session,
    url: str,
    referer: str,
    timeout: int = 60,
) -> Optional[requests.Response]:
    """GET with a single cookie-refresh retry on 401/403.

    NSE intermittently 401/403s even with valid cookies. One retry after
    re-hitting the referer page resolves the vast majority of cases.
    """
    headers = {**_BASE_HEADERS, "Referer": referer}
    try:
        resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        logger.error("Request failed for %s: %s", url, exc)
        return None

    if resp.status_code in (401, 403):
        logger.warning(
            "HTTP %s for %s; retrying once after cookie refresh", resp.status_code, url
        )
        try:
            session.get(referer, headers=_BASE_HEADERS, timeout=timeout)
            time.sleep(1)
            resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            logger.error("Retry failed for %s: %s", url, exc)
            return None

    if resp.status_code != 200:
        logger.error("Non-200 (%s) for %s", resp.status_code, url)
        return None

    return resp


def fetch_participant_oi(
    date_obj: Optional[datetime] = None,
    session: Optional[requests.Session] = None,
) -> Optional[pd.DataFrame]:
    """FII / DII / Pro / Client OI split. Returns a raw DataFrame (NSE column names)."""
    date_obj = date_obj or datetime.now()
    own_session = session is None
    session = session or _new_session()
    try:
        url = _format_url(_URLS["fao_participant_oi"], date_obj)
        resp = _get_with_retry(session, url, referer="https://www.nseindia.com/")
        if resp is None:
            return None
        # Row 0 is a free-text title ("PARTICIPANT WISE OPEN INTEREST... AS ON ..."),
        # real headers are row 1.
        return pd.read_csv(io.BytesIO(resp.content), skiprows=1)
    finally:
        if own_session:
            session.close()


def fetch_participant_vol(
    date_obj: Optional[datetime] = None,
    session: Optional[requests.Session] = None,
) -> Optional[pd.DataFrame]:
    """FII / DII / Pro / Client volume split. Returns a raw DataFrame (NSE column names)."""
    date_obj = date_obj or datetime.now()
    own_session = session is None
    session = session or _new_session()
    try:
        url = _format_url(_URLS["fao_participant_vol"], date_obj)
        resp = _get_with_retry(session, url, referer="https://www.nseindia.com/")
        if resp is None:
            return None
        # Same title-row quirk as participant_oi.
        return pd.read_csv(io.BytesIO(resp.content), skiprows=1)
    finally:
        if own_session:
            session.close()


def fetch_combine_oi(
    date_obj: Optional[datetime] = None,
    session: Optional[requests.Session] = None,
) -> Optional[pd.DataFrame]:
    """MWPL combined OI archive. Comes back as a zip containing a single CSV."""
    date_obj = date_obj or datetime.now()
    own_session = session is None
    session = session or _new_session()
    try:
        url = _format_url(_URLS["combine_oi"], date_obj)
        resp = _get_with_retry(session, url, referer="https://www.nseindia.com/")
        if resp is None:
            return None
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                logger.error("combine_oi zip contained no CSV")
                return None
            with zf.open(csv_names[0]) as fp:
                return pd.read_csv(fp)
    except zipfile.BadZipFile:
        logger.error("combine_oi response was not a valid zip (likely not yet published for this date)")
        return None
    finally:
        if own_session:
            session.close()


def normalize_participant_df(df: pd.DataFrame) -> pd.DataFrame:
    """Map NSE's native participant OI/vol columns to snake_case.

    NSE ships these with inconsistent header casing/spacing release to
    release, so normalize defensively. Adjust the rename map below to
    match whatever oi_analysis1.py's schema expects downstream.
    """
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_").replace("-", "_") for c in df.columns]
    # First column is the client-type label (FII / DII / Pro / Client / TOTAL),
    # not a numeric field. Give it a stable name regardless of NSE's exact wording.
    if len(df.columns):
        df = df.rename(columns={df.columns[0]: "client_type"})
    return df


def normalize_combine_oi_df(df: pd.DataFrame) -> pd.DataFrame:
    """Same idea for the combine_oi (MWPL) file."""
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_").replace("-", "_") for c in df.columns]
    return df


def save_eod_datasets(
    datasets: dict,
    date_obj: datetime,
    out_dir: Optional[Path] = None,
) -> dict:
    """Persist each normalized DataFrame to parquet, keyed by date.

    Layout: {out_dir}/{dataset_name}/{dataset_name}_{yyyymmdd}.parquet
    Datasets that failed to fetch (None) are skipped, not written as empty files.
    Requires pyarrow (or fastparquet) installed: pip install pyarrow

    Returns a dict of dataset_name -> written file path (or None if skipped).
    """
    out_dir = out_dir or DATA_DIR
    date_str = date_obj.strftime("%Y%m%d")
    written = {}

    for name, df in datasets.items():
        if df is None:
            written[name] = None
            continue
        subdir = out_dir / name
        subdir.mkdir(parents=True, exist_ok=True)
        path = subdir / f"{name}_{date_str}.parquet"
        try:
            df.to_parquet(path, index=False)
            written[name] = str(path)
            logger.info("Saved %s (%d rows) to %s", name, len(df), path)
        except Exception as exc:
            logger.error("Failed to save %s to parquet: %s", name, exc)
            written[name] = None

    return written


def fetch_all_eod(
    date_obj: Optional[datetime] = None,
    save: bool = False,
    out_dir: Optional[Path] = None,
    skip_non_trading_days: bool = True,
) -> dict:
    """Convenience entry point: fetch + normalize all three datasets in one
    NSE session. Call this once from engine.py's EOD job.

    Returns a dict of DataFrames, keyed by dataset name. Missing/failed
    fetches map to None rather than raising, so a single bad endpoint
    doesn't kill the whole EOD run.
    """
    date_obj = date_obj or datetime.now()

    if skip_non_trading_days and not is_trading_day(date_obj):
        logger.info("%s is not an NSE trading day - skipping EOD fetch", date_obj.strftime("%Y-%m-%d"))
        return {"fao_participant_oi": None, "fao_participant_vol": None, "combine_oi": None}

    session = _new_session()
    try:
        participant_oi = fetch_participant_oi(date_obj, session=session)
        participant_vol = fetch_participant_vol(date_obj, session=session)
        combine_oi = fetch_combine_oi(date_obj, session=session)
    finally:
        session.close()

    normalized = {
        "fao_participant_oi": normalize_participant_df(participant_oi) if participant_oi is not None else None,
        "fao_participant_vol": normalize_participant_df(participant_vol) if participant_vol is not None else None,
        "combine_oi": normalize_combine_oi_df(combine_oi) if combine_oi is not None else None,
    }

    if save:
        save_eod_datasets(normalized, date_obj, out_dir=out_dir)

    return normalized


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    results = fetch_all_eod(save=True)
    for name, frame in results.items():
        if frame is None:
            print(f"[FAILED] {name}")
        else:
            print(f"[OK] {name}: {frame.shape[0]} rows -> data/eod/{name}/")
