"""
nse_bse_fundamentals.py
========================
Trimmed from the original market_api.py. Keeps ONLY what genuinely has no
SmartAPI equivalent — market breadth and fundamental ratios that brokers
don't publish in a tick/quote feed. Everything else that market_api.py
used to fetch (VIX, BSE F&O, contract discovery, option chain, futures)
now has a SmartAPI-only equivalent in smartapi_market_data.py and has been
DELETED from here, not just left unused — see "Removed in this refactor"
below for the paper trail.

Kept
----
  - fetch_all_indices_snapshot()  — advances/declines/unchanged, P/E, P/B,
    dividend yield, 52-week/365d/30d % change, free-float market cap (ffmc)
    via fetch_all_indices() / parse_index_records(). None of this is
    standard broker quote data — it's NSE's own computed analytics.

Removed in this refactor (already covered by your existing smartapi_client.py)
--------------------------------------------------------------------------------
  - get_unified_market_data() VIX lookup  → smartapi_client.get_index_quote("INDIA VIX")
  - fetch_bse_index_quote()               → smartapi_client.get_index_quote("SENSEX"/"BANKEX")
  - fetch_bse_json_options()              → smartapi_client.get_atm_chain(..., exchange="BFO")
  - fetch_bse_futures()                   → smartapi_client (BFO futures via ScripMaster + get_ltp)
  - fetch_contract_info()                 → smartapi_client.list_expiries() + _scrip_indexes["strikes"]
  - fetch_option_chain() / parse_option_chain_response()
                                           → smartapi_client.get_atm_chain() / get_full_option_chain()
  - fetch_nifty_futures()                 → smartapi_client (FUTIDX rows via ScripMaster + get_ltp)

Kept but flagged as a fallback-only candidate, not deleted, since it still
duplicates SmartAPI's WS tick stream for the same symbols:
  - fetch_all_indices() / parse_index_records() per-stock OHLC rows — the
    Top Drivers/Draggers ffmc-weight calc needs `ffmc`, which nothing else
    provides, so this stays even though LTP/OHLC here overlaps with WS ticks.

If you're not actually using the breadth/fundamentals widget or Top
Drivers/Draggers panel, this whole file — and the NSE session-management
machinery (ensure_session, nse_request, cookie warm-up) it depends on — can
be dropped entirely and market_api.py retired.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import quote
import time

import requests
import pandas as pd


# ── Shared helpers ───────────────────────────────────────────────────────────

def safe_float(val) -> float:
    if val is None:
        return 0.0
    s = str(val).strip().replace(",", "")
    if s in ("-", "—", "", "0.000000000000000000000000"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _cache_bust(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_={datetime.now():%Y%m%d%H%M%S}"


# ── NSE session management ───────────────────────────────────────────────────
# Unchanged from market_api.py — still needed because /api/allIndices sits
# behind the same Akamai/cookie wall as everything else on NSE's site.

SESSION_TTL_MINS = 18

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control":   "no-cache",
    "Pragma":          "no-cache",
}

_session: requests.Session | None = None
_session_expiry: datetime | None = None


def ensure_session(force: bool = False) -> requests.Session:
    global _session, _session_expiry

    if (not force
            and _session is not None
            and _session_expiry
            and datetime.now() < _session_expiry
            and len(_session.cookies) > 0):
        return _session

    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    warm_up_urls = [
        ("https://www.nseindia.com/", {}),
        ("https://www.nseindia.com/market-data/live-equity-market",
         {"Referer": "https://www.nseindia.com/"}),
    ]
    for url, extra in warm_up_urls:
        try:
            session.get(url, headers=extra, timeout=20)
        except requests.RequestException as e:
            print(f"[ensure_session] warm-up failed for {url}: {e}")

    _session = session
    _session_expiry = datetime.now() + timedelta(minutes=SESSION_TTL_MINS)
    return _session


def _api_headers(referer: str = "") -> dict:
    h = dict(BASE_HEADERS)
    h["Accept"] = "application/json, text/plain, */*"
    h["X-Requested-With"] = "XMLHttpRequest"
    if referer:
        h["Referer"] = referer
    return h


def nse_request(url: str, referer: str = "", _retried: bool = False, _conn_retries: int = 2):
    session = ensure_session()
    try:
        resp = session.get(url, headers=_api_headers(referer), timeout=20)
    except requests.RequestException as e:
        if _conn_retries > 0:
            wait = 1.5 * (3 - _conn_retries)
            print(f"[nse_request] request error: {e} — retrying in {wait:.1f}s ({_conn_retries} left)")
            time.sleep(wait)
            return nse_request(url, referer, _retried=_retried, _conn_retries=_conn_retries - 1)
        print(f"[nse_request] request error: {e} — out of retries")
        return None

    if resp.status_code in (401, 403) and not _retried:
        print(f"[nse_request] HTTP {resp.status_code} — re-warming session and retrying")
        ensure_session(force=True)
        return nse_request(url, referer, _retried=True, _conn_retries=_conn_retries)

    if resp.status_code != 200:
        print(f"[nse_request] HTTP {resp.status_code} for {url}")
        return None

    try:
        return resp.json()
    except ValueError:
        return None


# ── Index fundamentals / breadth ─────────────────────────────────────────────

def fetch_all_indices_snapshot() -> pd.DataFrame:
    """
    Fetch https://www.nseindia.com/api/allIndices — the ONLY reason this
    file still exists. Covers breadth (advances/declines/unchanged) and
    fundamentals (P/E, P/B, dividend yield, 52-week/365d/30d % change)
    across every NSE index category. No broker feed publishes this.
    """
    url = _cache_bust("https://www.nseindia.com/api/allIndices")
    data = nse_request(url, referer="https://www.nseindia.com/")
    if not data:
        print("[fetch_all_indices_snapshot] API returned no data")
        return pd.DataFrame()

    rows = []
    for rec in data.get("data", []):
        rows.append({
            "key":           str(rec.get("key", "")),
            "index":         str(rec.get("index", "")),
            "indexSymbol":   str(rec.get("indexSymbol", "")),
            "last":          safe_float(rec.get("last")),
            "previousClose": safe_float(rec.get("previousClose")),
            "percentChange": safe_float(rec.get("percentChange")),
            "yearHigh":      safe_float(rec.get("yearHigh")),
            "yearLow":       safe_float(rec.get("yearLow")),
            "perChange365d": safe_float(rec.get("perChange365d")),
            "perChange30d":  safe_float(rec.get("perChange30d")),
            "pe":            str(rec.get("pe", "0")),
            "pb":            str(rec.get("pb", "0")),
            "dy":            str(rec.get("dy", "0")),
            "advances":      int(rec.get("advances", 0) or 0),
            "declines":      int(rec.get("declines", 0) or 0),
            "unchanged":     int(rec.get("unchanged", 0) or 0),
        })

    df = pd.DataFrame(rows)
    print(f"[fetch_all_indices_snapshot] {len(df)} indices fetched "
          f"across {df['key'].nunique() if not df.empty else 0} categories")
    return df


# ── Per-stock constituent data (ffmc for index-weight calc) ─────────────────
# Kept only because `ffmc` (free-float market cap) has no SmartAPI
# equivalent and feeds the Top Drivers/Draggers widget. LTP/OHLC here
# duplicates the WS tick stream — if you don't need per-stock weight, drop
# this whole section too.

FNO_STOCK_INDEX = "SECURITIES IN F&O"
DEFAULT_INDICES = [
    "NIFTY 50", "NIFTY BANK", "NIFTY MIDCAP SELECT",
    "NIFTY FIN SERVICE", "NIFTY NEXT 50", FNO_STOCK_INDEX,
]


def build_fno_url(index_name: str) -> str:
    encoded = quote(index_name)
    if index_name.strip().upper() == FNO_STOCK_INDEX:
        url = f"https://www.nseindia.com/api/equity-stockIndex?index={encoded}"
    else:
        url = f"https://www.nseindia.com/api/equity-stock-indices?index={encoded}"
    return _cache_bust(url)


def fetch_fno_index(index_name: str):
    return nse_request(
        build_fno_url(index_name),
        referer="https://www.nseindia.com/market-data/live-equity-market",
    )


def parse_index_records(json_data: dict, index_label: str) -> list[dict]:
    """Trimmed to just Symbol + ffmc — everything else (LTP, OHLC, volume)
    is redundant with the SmartAPI WS tick stream and has been dropped."""
    if not json_data:
        return []
    rows = []
    for rec in json_data.get("data", []):
        symbol = str(rec.get("symbol", "")).strip()
        if not symbol:
            continue
        rows.append({
            "Index":  index_label,
            "Symbol": symbol,
            "ffmc":   rec.get("ffmc"),
        })
    return rows


def fetch_ffmc_weights(indices: list[str] | None = None) -> pd.DataFrame:
    """Direct replacement for the ffmc-only slice of market_api.py's
    fetch_all_indices(). Used solely for Top Drivers/Draggers index-weight
    calc — everything else that function used to fetch now comes off the
    SmartAPI WS stream."""
    indices = indices or DEFAULT_INDICES
    rows: list[dict] = []

    def _worker(idx):
        data = fetch_fno_index(idx)
        return parse_index_records(data, idx) if data else []

    with ThreadPoolExecutor(max_workers=min(len(indices), 7)) as ex:
        futures = {ex.submit(_worker, idx): idx for idx in indices}
        for future in as_completed(futures):
            try:
                rows.extend(future.result())
            except Exception as e:
                print(f"[fetch_ffmc_weights] thread error for {futures[future]}: {e}")

    return pd.DataFrame(rows)


# ── __main__ smoke-test ───────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("NSE — allIndices breadth/fundamentals snapshot")
    snap_df = fetch_all_indices_snapshot()
    if not snap_df.empty:
        print(snap_df[["key", "index", "advances", "declines", "pe", "dy"]].head(10).to_string(index=False))

    print("\nNSE — ffmc weights (F&O universe)")
    ffmc_df = fetch_ffmc_weights([FNO_STOCK_INDEX])
    if not ffmc_df.empty:
        print(ffmc_df.head(10).to_string(index=False))
    print("=" * 60)
