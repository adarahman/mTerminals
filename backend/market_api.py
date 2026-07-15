"""
market_api.py
=============
Unified NSE + BSE data-fetch layer.

Sections
--------
  1. Imports & shared constants
  2. Shared helpers          — safe_float, get_or_create_sheet, _col_letter, _cache_bust
  3. NSE session management  — ensure_session, _api_headers, nse_request
  4. NSE fetch functions     — option chain, futures, VIX, indices, contract info
  5. NSE Excel writers       — write_all_indices_sheet, write_futures_sheet, …
  6. BSE fetch functions     — fetch_bse_json_options, fetch_bse_futures
  7. BSE Excel writer        — dump_bse_to_excel
  8. __main__ smoke-test
"""

# ── 1. Imports ────────────────────────────────────────────────────────────────

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import quote
import time

import requests
import pandas as pd

# xlwings is only needed when writing to Excel; imported lazily where required
# so the module can be imported in headless / server contexts without errors.


# ── 2. Shared helpers ─────────────────────────────────────────────────────────

def safe_float(val) -> float:
    """Coerce any BSE / NSE JSON value to float; returns 0.0 on failure."""
    if val is None:
        return 0.0
    s = str(val).strip().replace(",", "")
    if s in ("-", "—", "", "0.000000000000000000000000"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def get_or_create_sheet(wb, sheet_name):
    """Return existing xlwings sheet or create it if absent."""
    if sheet_name not in [s.name for s in wb.sheets]:
        print(f"'{sheet_name}' not found — creating it.")
        return wb.sheets.add(name=sheet_name)
    return wb.sheets[sheet_name]


def _col_letter(col_num: int) -> str:
    """Convert 1-based column number to Excel letter(s)."""
    letters = ""
    while col_num > 0:
        col_num, rem = divmod(col_num - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cache_bust(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_={datetime.now():%Y%m%d%H%M%S}"


# ── 3. NSE session management ─────────────────────────────────────────────────

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
_session_expiry: datetime | None  = None


def ensure_session(force: bool = False) -> requests.Session:
    """Return a warmed-up NSE session, re-creating it when stale or forced."""
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
        ("https://www.nseindia.com/",                          {}),
        ("https://www.nseindia.com/option-chain",              {"Referer": "https://www.nseindia.com/"}),
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
    """GET a NSE JSON endpoint; auto-retries once on 401/403, and up to
    ``_conn_retries`` times (short backoff, no re-warm) on connection-level
    errors — resets, timeouts — since those are typically a transient
    Akamai/rate-limit blip rather than a dead session, and previously fell
    straight through to a hard failure with zero retry."""
    session = ensure_session()
    try:
        resp = session.get(url, headers=_api_headers(referer), timeout=20)
    except requests.RequestException as e:
        if _conn_retries > 0:
            wait = 1.5 * (3 - _conn_retries)  # 1.5s, then 3s
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


# ── 4. NSE fetch functions ────────────────────────────────────────────────────

FNO_STOCK_INDEX = "SECURITIES IN F&O"

INDEX_RENAME = {
    "NIFTY 50":                  "NIFTY",
    "NIFTY NEXT 50":             "NIFTYNEXT50",
    "NIFTY BANK":                "BANKNIFTY",
    "NIFTY FINANCIAL SERVICES":  "FINNIFTY",
    "NIFTY FIN SERVICE":         "FINNIFTY",  # DEFAULT_INDICES/df_idx spelling
    "NIFTY MIDCAP SELECT":       "MIDCPNIFTY",
}

# The NSE /api/allIndices "index" names get_unified_market_data() matches
# against to build ticker_payload (feeds both the top-bar ticker pills for
# NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY *and*, via the same payload's
# "Change"/"% Change"/"Prev Close" fields, engine.py's day-change fallback
# for the active/primary symbol).
# NOTE: this was previously referenced but never defined anywhere in this
# file — every call to get_unified_market_data() raised a NameError on the
# first non-VIX index row, which was being swallowed upstream, leaving
# vix_pchange stuck at its 0.0 default and ticker_payload permanently
# empty. That's why % Change wasn't reflecting for the primary index or
# the secondary ticker pills.
TICKER_INDEX_WHITELIST = {
    "NIFTY 50", "NIFTY BANK", "NIFTY FINANCIAL SERVICES", "NIFTY MIDCAP SELECT",
}

# Same four pills, but keyed the way DEFAULT_INDICES/fetch_fno_index name
# them (df_idx's "Index" column stores the literal index_name passed to
# fetch_fno_index — "NIFTY FIN SERVICE", not allIndices' "NIFTY FINANCIAL
# SERVICES"). Used by build_ticker_payload_from_df_idx() below.
TICKER_SOURCE_INDICES = {"NIFTY 50", "NIFTY BANK", "NIFTY MIDCAP SELECT", "NIFTY FIN SERVICE"}

DEFAULT_INDICES = [
    "NIFTY 50", "NIFTY BANK", "NIFTY MIDCAP SELECT",
    "NIFTY FIN SERVICE", "NIFTY NEXT 50",
    FNO_STOCK_INDEX,
]
# NOTE: "AllIndices" was removed from this list on 2026-07-04. It was a 7th
# threaded request per tick, added only so df_idx would contain an
# "INDIA VIX" row for engine.py's fallback lookup — but option_chain_json.py
# already gets VIX every tick via get_unified_market_data() (engine.py only
# uses the df_idx fallback when india_vix <= 0). That made the "AllIndices"
# fetch pure duplicate NSE load with no effect on the final result. VIX now
# has exactly one source: get_unified_market_data() (fetch_india_vix(), a
# separate function that hit this same endpoint a second time per tick,
# was removed on 2026-07-08 as fully redundant — see get_unified_market_data()).


def rename_index(symbol: str) -> str:
    return INDEX_RENAME.get(symbol.strip().upper(), symbol)


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


def fetch_all_data(itype: str, symbol: str, expiry: str, fno_index=None) -> dict:
    """Fetch option chain + optional FNO index data in one call."""
    result = {"oc": None, "fno": None}

    if symbol:
        oc_url = _cache_bust(
            f"https://www.nseindia.com/api/option-chain-v3"
            f"?type={itype}&symbol={symbol}&expiry={expiry}"
        )
        result["oc"] = nse_request(oc_url, referer="https://www.nseindia.com/option-chain")

    if fno_index:
        result["fno"] = fetch_fno_index(fno_index)

    return result


def parse_index_records(json_data: dict, index_label: str) -> list[dict]:
    if not json_data:
        return []

    now_str = datetime.now().strftime("%d-%b-%y %H:%M:%S")
    rows = []

    for rec in json_data.get("data", []):
        symbol = str(rec.get("symbol", "")).strip()
        if not symbol:
            continue

        ttv = rec.get("totalTradedValue") or 0
        value_cr = round(ttv / 10_000_000, 2) if ttv else 0

        last_update = str(rec.get("lastUpdateTime") or "").strip()
        if not last_update or last_update.startswith("31"):
            last_update = now_str

        rows.append({
            "Index":          index_label,
            "Symbol":         rename_index(symbol),
            "Series":         rec.get("series", ""),
            "Open":           rec.get("open"),
            "Day High":       rec.get("dayHigh"),
            "Day Low":        rec.get("dayLow"),
            "Last Price":     rec.get("lastPrice"),
            "Prev Close":     rec.get("previousClose"),
            "Change":         rec.get("change"),
            "% Change":       rec.get("pChange"),
            "Volume":         rec.get("totalTradedVolume"),
            "Value (Cr)":     value_cr,
            # Raw rupee-scale value (not divided/rounded for display) — kept
            # alongside "Value (Cr)" so VWAP (Value/Volume) can be computed
            # to full precision. dashboard.js's price chart VWAP overlay
            # reads this + Volume via option_chain_json.py's df_idx merge.
            "Value":          ttv,
            "52W High":       rec.get("yearHigh"),
            "52W Low":        rec.get("yearLow"),
            "Near 52W High %": rec.get("nearWKH"),
            "Near 52W Low %":  rec.get("nearWKL"),
            "% Chg 365d":     rec.get("perChange365d"),
            "% Chg 30d":      rec.get("perChange30d"),
            "Last Traded Time": last_update,
            # Free-float market cap — used to derive live index weight
            # (weight_i = ffmc_i / sum(ffmc) for stocks sharing the same
            # "Index" tag) for the Top Drivers/Draggers widget. Not used
            # anywhere else in the pipeline, safe to add.
            "ffmc":           rec.get("ffmc"),
        })

    return rows


def fetch_all_indices(indices: list[str] | None = None) -> pd.DataFrame:
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
                print(f"[fetch_all_indices] thread error for {futures[future]}: {e}")

    return pd.DataFrame(rows)


def build_ticker_payload_from_df_idx(df_idx: pd.DataFrame) -> list[dict]:
    """Derive the NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY ticker-strip pills
    from df_idx (the DataFrame fetch_all_indices(DEFAULT_INDICES) already
    builds every tick) instead of a second /api/allIndices round-trip.

    Each fetch_fno_index(idx) call's raw JSON carries the index's own quote
    as its first record (priority 1, series=None) alongside the constituent
    stocks (priority 0, series="EQ") — parse_index_records() already keeps
    that self-row in df_idx, it's just gone unused until now. Series being
    NaN/None is what tells a self-row apart from a constituent-stock row.
    """
    if df_idx is None or df_idx.empty:
        return []

    payload = []
    for idx in TICKER_SOURCE_INDICES:
        self_rows = df_idx[(df_idx["Index"] == idx) & (df_idx["Series"].isna())]
        if self_rows.empty:
            continue
        row = self_rows.iloc[0]
        payload.append({
            "Symbol":        INDEX_RENAME.get(idx.upper(), idx),
            "BackendSymbol": INDEX_RENAME.get(idx.upper(), idx),
            "Last Price":    row["Last Price"],
            "% Change":      row["% Change"],
            "Change":        row["Change"],
            "Prev Close":    row["Prev Close"],
        })
    return payload


def fetch_all_market_indices() -> list[dict]:
    """Fetch all indices (Nifty, BankNifty, VIX, etc.) in one payload."""
    url = _cache_bust("https://www.nseindia.com/api/allIndices")
    data = nse_request(url, referer="https://www.nseindia.com/")

    if not data or "data" not in data:
        return []

    # Return the full list of index records
    return data["data"]
    
def get_unified_market_data(df_idx: pd.DataFrame | None = None):
    """Return (VIX_value, VIX_pct_change, Ticker_payload).

    VIX_value is None if VIX wasn't found/parsed this tick — callers should
    treat None the same as a failed fetch (see engine.py's india_vix <= 0
    fallback to 15.0).

    df_idx is the DataFrame fetch_all_indices(DEFAULT_INDICES) already
    builds every tick (via fetch_fno_index, which returns each index's own
    quote as its first record alongside the constituent stocks). Pass it in
    and the NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY ticker pills come from that
    — no second network call needed for them.

    /api/allIndices is still hit, but *only* for INDIA VIX now — VIX doesn't
    appear anywhere in the equity-stock-indices payload DEFAULT_INDICES
    already fetches, so there's no way to avoid this one. If df_idx isn't
    passed, ticker_payload comes back empty (VIX lookup is unaffected).

    SENSEX/BANKEX pills are NOT covered here (BSE indices don't appear in
    NSE's allIndices) — see fetch_bse_index_quote() below for those.
    """
    all_indices = fetch_all_market_indices()

    # None (not 15.0) on a failed/missing match — this used to be split out
    # into a separate fetch_india_vix() call specifically so callers could
    # tell "fetch genuinely failed" apart from "VIX is quietly at 15", but
    # that distinction never survived the trip through option_chain_json.py's
    # `_live_vix = fut_vix.result() or 0.0`, which collapses None -> 0.0
    # before engine.py's own `india_vix <= 0` check falls back to 15.0 anyway.
    vix_value = None
    vix_pchange = 0.0

    for idx in all_indices:
        name = str(idx.get("index", "")).strip().upper()
        if "VIX" in name:
            last = idx.get("last")
            if last is not None:
                try:
                    vix_value = float(last)
                except (TypeError, ValueError):
                    pass
            pchg = idx.get("percentChange")
            if pchg is not None:
                try:
                    vix_pchange = float(pchg)
                except (TypeError, ValueError):
                    pass
            break

    ticker_payload = build_ticker_payload_from_df_idx(df_idx)

    if df_idx is not None and not df_idx.empty and not ticker_payload:
        print(f"[get_unified_market_data] WARNING: 0 ticker pills matched "
              f"{sorted(TICKER_SOURCE_INDICES)} in df_idx — check that "
              f"DEFAULT_INDICES still contains those exact index names.")

    return vix_value, vix_pchange, ticker_payload
# BSE scrip codes for the two BSE F&O indices we track on the ticker strip.
_BSE_INDEX_SCRIP_CD = {"SENSEX": "1", "BANKEX": "2"}
def fetch_bse_index_quote(symbol: str) -> dict | None:
    """Return a ticker-strip entry for a BSE index (SENSEX/BANKEX), in the
    same {"Symbol", "Last Price", "% Change"} shape get_unified_market_data()
    produces for NSE indices — so both can be merged into one ticker_list
    and renderIndexTicker() on the frontend doesn't need to know which
    exchange a pill's data came from.

    Uses BSE's getScripHeaderData endpoint (same one the site's own index
    quote widgets read), which gives LTP + previous close directly instead
    of us having to approximate % change from the futures NetChange.
    """
    scrip_cd = _BSE_INDEX_SCRIP_CD.get(symbol.upper())
    if not scrip_cd:
        return None

    data = bse_request(
        "https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w",
        params={"Debtflag": "", "scripcode": scrip_cd, "seriesid": ""},
    )
    if not data:
        return None

    curr_rate = data.get("CurrRate") or {}
    header = data.get("Header") or {}

    try:
        # BSE nests everything one level down: LTP/Chg/PcChg live under
        # "CurrRate", PrevClose lives under "Header". Fall back to Header's
        # LTP/PrevClose if CurrRate is ever missing a field.
        ltp = safe_float(curr_rate.get("LTP") or header.get("LTP"))
        prev_close = safe_float(header.get("PrevClose"))
        pct_change = safe_float(curr_rate.get("PcChg"))
        change = safe_float(curr_rate.get("Chg"))

        # safe_float() returns 0.0 (not None) on a missing/unparseable field,
        # so a real "no change today" 0.0 and a parse failure look identical
        # here — but recomputing from ltp/prev_close in that case yields the
        # same 0.0 anyway, so falling back on falsy is safe.
        if not pct_change and prev_close:
            pct_change = ((ltp - prev_close) / prev_close) * 100
        if not change and prev_close:
            change = round(ltp - prev_close, 2)
    except Exception as e:
        print(f"[fetch_bse_index_quote] parse error for {symbol}: {e}")
        return None

    if not ltp:
        print(f"[fetch_bse_index_quote] {symbol}: got response but LTP/CurrRate/PrevClose "
              f"all empty — raw response: {data}")
        return None

    return {
        "Symbol": symbol.upper(),
        "Last Price": ltp,
        "% Change": pct_change,
        "Change": change,
    }
def fetch_nifty_futures(index: str = "nse50_fut") -> pd.DataFrame:
    url = f"https://www.nseindia.com/api/liveEquity-derivatives?index={index}"
    data = nse_request(url, referer="https://www.nseindia.com/")
    if not data:
        return pd.DataFrame()

    rows = []
    for rec in data.get("data", []):
        ltp  = rec.get("lastPrice") or 0
        spot = rec.get("underlyingValue") or 0

        # "volume" was never a real field on NSE's derivative-quote schema —
        # NSE names it numberOfContractsTraded (contracts) alongside
        # totalTurnover (rupee turnover for those contracts), same pattern
        # as the priceInfo/tradeInfo split on the equity side. rec.get
        # ("volume") was silently returning None here the whole time, the
        # same class of bug as the index Value/Volume VWAP mixup — just
        # unnoticed because nothing rendered this column yet.
        #
        # Turnover is what makes this contract's own VWAP legitimate
        # (Turnover / Volume), unlike the index case: futures are an
        # actually-traded instrument, so their own turnover/volume ratio
        # IS this contract's session VWAP — no basket-aggregation issue.
        # Falling back through a few plausible key spellings defensively
        # since this hasn't been confirmed against a live tick yet — print
        # rec.keys() on a live run and drop the ones that don't hit.
        volume = (rec.get("numberOfContractsTraded")
                  or rec.get("tradedVolume")
                  or rec.get("volume"))
        turnover = (rec.get("totalTurnover")
                    or rec.get("turnover")
                    or rec.get("tradedValue"))

        # Self-diagnosing rather than silently-wrong: if every guessed key
        # missed, print the actual keys once so the real field name is
        # obvious on the first live run instead of hiding as another
        # quiet None (same failure mode fetch_bse_index_quote() guards
        # against with its own "got response but ... all empty" print).
        if volume is None or turnover is None:
            print(f"[fetch_nifty_futures] {rec.get('contract')}: "
                  f"volume={volume} turnover={turnover} — guessed keys missed, "
                  f"raw fields available: {sorted(rec.keys())}")

        rows.append({
            "Contract":   rec.get("contract"),
            "Underlying": rec.get("underlying"),
            "Expiry":     rec.get("expiryDate"),
            "LTP":        ltp,
            "Change":     rec.get("change"),
            "PctChange":  rec.get("pChange"),
            "Open":       rec.get("openPrice"),
            "High":       rec.get("highPrice"),
            "Low":        rec.get("lowPrice"),
            "PrevClose":  rec.get("closePrice"),
            "Volume":     volume,
            # Raw rupee-scale turnover (NOT pre-divided into crore) so a
            # VWAP = Turnover/Volume can be computed to full precision,
            # same convention as parse_index_records()'s "Value" field.
            # UNVERIFIED UNIT: NSE derivative turnover has historically
            # been published in absolute rupees on some endpoints and in
            # lakhs on others — sanity-check the first live value against
            # LTP * Volume * lot_size before trusting the VWAP this feeds.
            "Turnover":   turnover,
            "OI":         rec.get("openInterest"),
            "Spot":       spot,
            "Basis":      round(ltp - spot, 2),
        })

    return pd.DataFrame(rows)


def fetch_contract_info(symbol: str) -> dict:
    url  = _cache_bust(f"https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}")
    data = nse_request(url, referer="https://www.nseindia.com/option-chain")

    if not data or "expiryDates" not in data:
        return {"expiry_dates": [], "strike_prices": []}

    strikes = [float(s) for s in data.get("strikePrice", []) if str(s).strip()]
    return {"expiry_dates": data.get("expiryDates", []), "strike_prices": strikes}



def fetch_option_chain(symbol: str, expiry: str,
                       itype: str = "Indices") -> dict:
    """
    Fetch NSE option chain v3, normalise the response into a standard
    ``{"records": {"expiryDates": [...], "data": [...], "underlyingValue": ...}}``
    envelope, and return it.

    Replaces the local ``get_option_chain()`` in option_chain.py.
    Uses the shared ``ensure_session`` / ``nse_request`` layer — no
    separate requests.Session() needed.

    Parameters
    ----------
    symbol : str  e.g. "NIFTY", "BANKNIFTY"
    expiry : str  "DD-Mon-YYYY" e.g. "30-Jun-2026"
    itype  : str  "Indices" (default) or "Equities"

    Returns
    -------
    dict  — normalised payload with a "records" key guaranteed present.

    Raises
    ------
    RuntimeError if the API returns an unusable response.
    """
    from urllib.parse import quote as _quote

    symbol_upper = symbol.strip().upper()
    url = _cache_bust(
        f"https://www.nseindia.com/api/option-chain-v3"
        f"?type={itype}&symbol={_quote(symbol_upper)}&expiry={_quote(expiry)}"
    )

    print(f"[market_api] Fetching option chain — {symbol_upper} {expiry}")
    payload = nse_request(url, referer="https://www.nseindia.com/option-chain")

    if payload is None:
        # nse_request already retried once; hard failure
        raise RuntimeError(
            f"NSE option chain returned no data for {symbol_upper} {expiry}. "
            "Check session / network."
        )

    # ── v3 flat-response adapter ──────────────────────────────────────────────
    # Some API versions return a flat dict (no "records" wrapper).
    if "records" not in payload:
        if "expiryDates" in payload or "data" in payload:
            print("[market_api] Adapting flat API-v3 response → records envelope")
            payload = {
                "records": {
                    "expiryDates": payload.get("expiryDates", [expiry]),
                    "data":        payload.get("data", []),
                    "underlyingValue": (
                        payload.get("underlyingValue")
                        or (payload.get("filtered", {}).get("data") or [{}])[0]
                               .get("underlyingValue", 0.0)
                    ),
                    "timestamp": payload.get("timestamp", ""),
                }
            }

    if "records" not in payload:
        raise RuntimeError(
            f"NSE response missing 'records' key. "
            f"Keys present: {list(payload.keys())}"
        )

    if not payload["records"].get("expiryDates"):
        raise RuntimeError(
            f"NSE returned empty expiryDates for {symbol_upper} {expiry}."
        )

    rec = payload["records"]
    print(
        f"[market_api] Option chain OK — "
        f"spot={rec.get('underlyingValue')}  "
        f"timestamp={rec.get('timestamp', '—')}  "
        f"expiries={len(rec.get('expiryDates', []))}"
    )
    return payload


def parse_option_chain_response(payload: dict, expiry: str) -> "pd.DataFrame":
    """
    Convert a normalised NSE option-chain payload (as returned by
    ``fetch_option_chain``) into the flat DataFrame that the rest of the
    pipeline (``build_master_table_nse``, ``build_engine_result``, …) expects.

    Column schema (matches the original parse_option_chain in option_chain.py):
        StrikePrice, Expiry,
        CE_OI, CE_ChgOI, CE_PctChgOI, CE_Volume, CE_IV, CE_LTP,
        CE_Change, CE_pChange,
        CE_BidQty, CE_BidPrice, CE_AskQty, CE_AskPrice,
        CE_BuyQty, CE_SellQty,
        PE_OI, PE_ChgOI, PE_PctChgOI, PE_Volume, PE_IV, PE_LTP,
        PE_Change, PE_pChange,
        PE_BidQty, PE_BidPrice, PE_AskQty, PE_AskPrice,
        PE_BuyQty, PE_SellQty,
        Spot, Symbol
    """
    records = payload["records"]

    rows = []
    for item in records.get("data", []):
        strike = item.get("strikePrice")
        ce = item.get("CE", {})
        pe = item.get("PE", {})

        spot_val = (pe.get("underlyingValue") if pe.get("underlyingValue")
                    else ce.get("underlyingValue"))
        sym_val  = (pe.get("underlying") if pe.get("underlying")
                    else ce.get("underlying"))

        rows.append({
            "StrikePrice":   strike,
            "Expiry":        expiry,
            "CE_OI":         ce.get("openInterest"),
            "CE_ChgOI":      ce.get("changeinOpenInterest"),
            "CE_PctChgOI":   ce.get("pchangeinOpenInterest"),
            "CE_Volume":     ce.get("totalTradedVolume"),
            "CE_IV":         ce.get("impliedVolatility"),
            "CE_LTP":        ce.get("lastPrice"),
            "CE_Change":     ce.get("change"),
            "CE_pChange":    ce.get("pChange"),
            "CE_BidQty":     ce.get("buyQuantity1"),
            "CE_BidPrice":   ce.get("buyPrice1"),
            "CE_AskQty":     ce.get("sellQuantity1"),
            "CE_AskPrice":   ce.get("sellPrice1"),
            "CE_BuyQty":     ce.get("totalBuyQuantity"),
            "CE_SellQty":    ce.get("totalSellQuantity"),
            "PE_OI":         pe.get("openInterest"),
            "PE_ChgOI":      pe.get("changeinOpenInterest"),
            "PE_PctChgOI":   pe.get("pchangeinOpenInterest"),
            "PE_Volume":     pe.get("totalTradedVolume"),
            "PE_IV":         pe.get("impliedVolatility"),
            "PE_LTP":        pe.get("lastPrice"),
            "PE_Change":     pe.get("change"),
            "PE_pChange":    pe.get("pChange"),
            "PE_BidQty":     pe.get("buyQuantity1"),
            "PE_BidPrice":   pe.get("buyPrice1"),
            "PE_AskQty":     pe.get("sellQuantity1"),
            "PE_AskPrice":   pe.get("sellPrice1"),
            "PE_BuyQty":     pe.get("totalBuyQuantity"),
            "PE_SellQty":    pe.get("totalSellQuantity"),
            "Spot":          spot_val,
            "Symbol":        sym_val,
        })

    return pd.DataFrame(rows)


# ── 5. NSE Excel writers ──────────────────────────────────────────────────────

_INDEX_BAND_COLOURS = {
    "NIFTY 50":           ((0, 112, 192), (255, 255, 255)),
    "NIFTY BANK":         ((0, 176,  80), (255, 255, 255)),
    "NIFTY FIN SERVICE":  ((255, 192,  0), (0, 0, 0)),
    "NIFTY MIDCAP SELECT":((112,  48, 160), (255, 255, 255)),
}


def write_all_indices_sheet(wb, df: pd.DataFrame, sheet_name: str = "AllIndices") -> None:
    """Batch-write all-indices data to Excel (macOS-safe, minimal COM round-trips)."""
    ws = get_or_create_sheet(wb, sheet_name)

    if df.empty:
        print("No index data to write.")
        return

    ws.range("A1").options(index=False).value = df

    n_cols     = len(df.columns)
    last_row   = len(df) + 1
    last_col   = _col_letter(n_cols)
    hdr_rng    = ws.range(f"A1:{last_col}1")
    hdr_rng.font.bold        = True
    hdr_rng.font.color       = (255, 255, 255)
    hdr_rng.color            = (0, 112, 192)

    try:
        hdr_rng.api.HorizontalAlignment = -4108          # xlCenter
    except AttributeError:
        hdr_rng.api.horizontal_alignment.set(-4108)

    # Band-colour the Index column cells
    for r_idx, val in enumerate(df["Index"].tolist(), start=2):
        band = _INDEX_BAND_COLOURS.get(val)
        if band:
            cell = ws.range((r_idx, 1))
            cell.color      = band[0]
            cell.font.color = band[1]

    ws.range(f"D2:I{last_row}").number_format = "#,##0.00"
    ws.range(f"K2:N{last_row}").number_format = "#,##0.00"
    ws.range(f"J2:J{last_row}").number_format = "0.00"
    ws.range(f"O2:R{last_row}").number_format = "0.00"

    change_col_idx    = df.columns.get_loc("Change")
    change_col_letter = _col_letter(change_col_idx + 1)
    for i, val in enumerate(df["Change"].tolist()):
        if isinstance(val, (int, float)):
            cell = ws.range((i + 2, change_col_idx + 1))
            cell.font.color = (0, 176, 80) if val > 0 else (255, 0, 0) if val < 0 else None

    print(f"[+] Wrote {len(df)} rows → '{sheet_name}'.")


def fetch_and_write_all_indices(wb, indices=None, sheet_name: str = "AllIndices") -> pd.DataFrame:
    df = fetch_all_indices(indices)
    write_all_indices_sheet(wb, df, sheet_name)
    return df


def write_futures_sheet(wb, df: pd.DataFrame, sheet_name: str = "FuturesData") -> None:
    ws = get_or_create_sheet(wb, sheet_name)
    if df.empty:
        print("No futures data to write.")
        return
    ws.range("A1").options(index=False).value = df
    print(f"[+] Wrote {len(df)} rows → '{sheet_name}'.")


def fetch_and_write_nifty_futures(wb, index: str = "nse50_fut",
                                   sheet_name: str = "FuturesData") -> pd.DataFrame:
    df = fetch_nifty_futures(index)
    write_futures_sheet(wb, df, sheet_name)
    return df


def fetch_and_write_expiry_dates(wb, symbol: str,
                                  sheet_name: str = "NSEHoliday", col: str = "H") -> list:
    info         = fetch_contract_info(symbol)
    expiry_dates = info["expiry_dates"]
    ws           = get_or_create_sheet(wb, sheet_name)

    ws.range(f"{col}1").value = "ExpiryDate"
    last_row = ws.range(f"{col}1048576").end("up").row
    if last_row > 1:
        ws.range(f"{col}2:{col}{last_row}").clear_contents()
    if expiry_dates:
        ws.range(f"{col}2").value = [[d] for d in expiry_dates]

    return expiry_dates


# ── 6. BSE fetch functions ────────────────────────────────────────────────────

BSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":          "https://www.bseindia.com",
    "Referer":         "https://www.bseindia.com/",
}


def bse_request(url: str, params: dict | None = None):
    """GET a BSE JSON endpoint (stateless, no session needed)."""
    try:
        resp = requests.get(url, headers=BSE_HEADERS, params=params, timeout=10)
        if resp.status_code != 200:
            print(f"[-] BSE HTTP {resp.status_code} for {url}")
            return None
        return resp.json()
    except Exception as e:
        print(f"[-] BSE request error: {e}")
        return None


def fetch_bse_json_options(expiry_str: str = "02 Jul 2026",
                            scrip_cd: str  = "1") -> tuple[pd.DataFrame | None, float]:
    """
    Fetch BSE option chain (SENSEX scrip_cd='1', BANKEX scrip_cd='2').
    Returns (DataFrame, spot_price).  DataFrame columns mirror NSE naming.
    """
    print(f"[*] BSE options — Expiry: {expiry_str}  scrip_cd: {scrip_cd}")

    data = bse_request(
        "https://api.bseindia.com/BseIndiaAPI/api/DerivOptionChain_IV/w",
        params={"Expiry": expiry_str, "scrip_cd": scrip_cd, "strprice": "0"},
    )

    if not data or "Table" not in data:
        print("[-] BSE options: no 'Table' key in response")
        return None, 0.0

    spot = 0.0
    rows = []

    for row in data["Table"]:
        try:
            strike = safe_float(row.get("Strike_Price1") or row.get("Strike_Price", 0))
            if strike <= 0:
                continue

            if spot == 0.0:
                ula = safe_float(row.get("UlaValue", 0))
                if ula > 0:
                    spot = ula

            rows.append({
                "CE_OI":         safe_float(row.get("C_Open_Interest")),
                "CE_ChgOI":      safe_float(row.get("C_Absolute_Change_OI")),
                "CE_Volume":     safe_float(row.get("C_Vol_Traded")),
                "CE_LTP":        safe_float(row.get("C_Last_Trd_Price")),
                "CE_Change":     safe_float(row.get("C_NetChange")),
                "CE_IV":         safe_float(row.get("C_IV")),
                "CE_BidQty":     safe_float(row.get("C_BIdQty")),
                "CE_BidPrice":   safe_float(row.get("C_BidPrice")),
                "CE_AskQty":     safe_float(row.get("C_OfferQty")),
                "CE_AskPrice":   safe_float(row.get("C_OfferPrice")),
                "CE_SeriesCode": row.get("C_Series_Code"),
                "CE_SeriesId":   row.get("C_Series_Id"),
                "Strike":        strike,
                "PE_IV":         safe_float(row.get("IV")),
                "PE_LTP":        safe_float(row.get("Last_Trd_Price")),
                "PE_Change":     safe_float(row.get("NetChange")),
                "PE_Volume":     safe_float(row.get("Vol_Traded")),
                "PE_ChgOI":      safe_float(row.get("Absolute_Change_OI")),
                "PE_OI":         safe_float(row.get("Open_Interest")),
                "PE_BidQty":     safe_float(row.get("BIdQty")),
                "PE_BidPrice":   safe_float(row.get("BidPrice")),
                "PE_AskQty":     safe_float(row.get("OfferQty")),
                "PE_AskPrice":   safe_float(row.get("OfferPrice")),
                "PE_SeriesCode": row.get("p_Series_Code"),
                "PE_SeriesId":   row.get("Series_Id"),
                "Timestamp":     row.get("End_TimeStamp"),
            })
        except Exception:
            continue

    if not rows:
        print("[-] BSE options: no valid rows")
        return None, 0.0

    df = pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)
    print(f"[+] BSE options: {len(df)} strikes  spot={spot}")
    _numeric_cols = [
        "CE_OI", "CE_ChgOI", "CE_Volume", "CE_LTP", "CE_Change", "CE_IV",
        "CE_BidQty", "CE_BidPrice", "CE_AskQty", "CE_AskPrice", "Strike",
        "PE_IV", "PE_LTP", "PE_Change", "PE_Volume", "PE_ChgOI", "PE_OI",
        "PE_BidQty", "PE_BidPrice", "PE_AskQty", "PE_AskPrice",
    ]
    df[_numeric_cols] = df[_numeric_cols].astype("float64")
    return df, spot


def _last_weekday_of_month(year: int, month: int, weekday: int) -> "datetime.date":
    """
    weekday: Monday=0 ... Sunday=6 (Python's date.weekday() convention).
    Returns the date of the last occurrence of `weekday` in the given month.
    """
    from calendar import monthrange
    last_day = monthrange(year, month)[1]
    d = datetime(year, month, last_day).date()
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


# BSE holiday shifting requires the actual NSE/BSE trading holiday calendar
# for the year, which changes annually and isn't hardcoded here. Wire this
# to your existing holiday list if you already track one elsewhere (e.g. for
# NSE expiry_manager.py) — otherwise this returns the *unadjusted* last
# Thursday, which is correct except in the rare month where that Thursday
# happens to be a market holiday.
def generate_bse_futures_expiry(reference_date=None, holidays: set | None = None) -> str:
    """
    Returns the current/upcoming BSE SENSEX/BANKEX monthly futures expiry
    as a string in the "%d %b %Y" format fetch_bse_futures() expects
    (e.g. "30 Jul 2026"), per BSE's post-Sept-2025 rule: last Thursday of
    the month, shifted to the previous trading day if that Thursday is a
    market holiday.

    reference_date defaults to today. If today is already past this
    month's last Thursday, rolls forward to next month's.
    """
    ref = reference_date or datetime.now().date()
    THURSDAY = 3

    expiry = _last_weekday_of_month(ref.year, ref.month, THURSDAY)
    if expiry < ref:
        # this month's expiry has passed — roll to next month
        next_month = ref.month + 1
        next_year = ref.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        expiry = _last_weekday_of_month(next_year, next_month, THURSDAY)

    if holidays:
        while expiry in holidays:
            expiry -= timedelta(days=1)

    return expiry.strftime("%d %b %Y")



def fetch_bse_futures(expiry_str: str = None,
                       scrip_cd: str  = "1") -> pd.DataFrame | None:
    """
    Fetch BSE Index Futures (SENSEX scrip_cd='1', BANKEX scrip_cd='2').
    Returns DataFrame or None.

    expiry_str defaults to the current/next BSE monthly expiry (last
    Thursday of the month, per post-Sept-2025 rules) via
    generate_bse_futures_expiry(), so this no longer goes stale month to
    month. Pass an explicit string to override (e.g. for a specific weekly
    contract).
    """
    if expiry_str is None:
        expiry_str = generate_bse_futures_expiry()
    print(f"[*] BSE futures — Expiry: {expiry_str}  scrip_cd: {scrip_cd}")

    data = bse_request(
        "https://api.bseindia.com/BseIndiaAPI/api/DerivOptionChain/w",
        params={"Expiry": expiry_str, "ProductType": "IF", "scrip_cd": scrip_cd},
    )

    if not data:
        return None

    table = data.get("Table", [])
    if not table:
        print("[-] BSE futures: empty Table")
        return None

    rows = [
        {
            "Expiry":   row.get("End_TimeStamp", expiry_str),
            "LTP":      safe_float(row.get("Last_Trd_Price")),
            "Change":   safe_float(row.get("NetChange")),
            "Volume":   safe_float(row.get("Vol_Traded")),
            "OI":       safe_float(row.get("Open_Interest")),
            "ChgOI":    safe_float(row.get("Absolute_Change_OI")),
            "UlaValue": safe_float(row.get("UlaValue")),
        }
        for row in table
    ]

    df = pd.DataFrame(rows)
    print(f"[+] BSE futures: {len(df)} rows")
    return df


# ── 7. BSE Excel writer ───────────────────────────────────────────────────────

def dump_bse_to_excel(expiry_str: str = "02 Jul 2026",
                       scrip_cd: str  = "1",
                       sheet_name: str = "BSE_Data") -> None:
    """
    Fetch live BSE option-chain data and write it to the active Excel workbook.
    xlwings is imported here so the module is usable in headless contexts.
    """
    import xlwings as xw

    df, spot = fetch_bse_json_options(expiry_str=expiry_str, scrip_cd=scrip_cd)

    if df is None or df.empty:
        print("[-] BSE Excel dump aborted: empty DataFrame.")
        return

    print(f"[*] BSE spot={spot}  Writing {len(df)} rows → '{sheet_name}'…")

    try:
        wb = xw.books.active
        ws = get_or_create_sheet(wb, sheet_name)
        ws.clear_contents()
        ws.range("A1").options(index=False).value = df
        print(f"[+] BSE Excel dump complete → '{sheet_name}'.")
    except Exception as e:
        print(f"[-] BSE Excel bridge failure: {e}")


# ── 8. __main__ smoke-test ────────────────────────────────────────────────────

def fetch_all_indices_snapshot() -> pd.DataFrame:
    """
    Fetch https://www.nseindia.com/api/allIndices and return a normalised
    DataFrame covering ALL index categories (Derivatives-eligible, Broad
    Market, Sectoral, Strategy, Thematic ...).

    Columns
    -------
    key             : category string  e.g. "BROAD MARKET INDICES"
    index           : display name     e.g. "INDIA VIX"
    indexSymbol     : short symbol     e.g. "INDIA VIX"
    last            : last traded price
    open            : day open
    high            : day high
    low             : day low
    previousClose   : previous close
    variation       : absolute change  (last - previousClose)
    percentChange   : % change
    yearHigh        : 52-week high
    yearLow         : 52-week low
    perChange365d   : % change vs 1 year ago
    perChange30d    : % change vs 30 days ago
    pe              : P/E ratio (string; "0" for VIX / strategy indices)
    pb              : P/B ratio (string)
    dy              : dividend yield (string)
    advances        : advancing stocks count
    declines        : declining stocks count
    unchanged       : unchanged stocks count

    Returns an empty DataFrame on failure.
    """
    url  = _cache_bust("https://www.nseindia.com/api/allIndices")
    data = nse_request(url, referer="https://www.nseindia.com/")
    if not data:
        print("[fetch_all_indices_snapshot] API returned no data")
        return pd.DataFrame()

    rows = []
    for rec in data.get("data", []):
        rows.append({
            "key":           str(rec.get("key",          "")),
            "index":         str(rec.get("index",        "")),
            "indexSymbol":   str(rec.get("indexSymbol",  "")),
            "last":          safe_float(rec.get("last")),
            "open":          safe_float(rec.get("open")),
            "high":          safe_float(rec.get("high")),
            "low":           safe_float(rec.get("low")),
            "previousClose": safe_float(rec.get("previousClose")),
            "variation":     safe_float(rec.get("variation")),
            "percentChange": safe_float(rec.get("percentChange")),
            "yearHigh":      safe_float(rec.get("yearHigh")),
            "yearLow":       safe_float(rec.get("yearLow")),
            "perChange365d": safe_float(rec.get("perChange365d")),
            "perChange30d":  safe_float(rec.get("perChange30d")),
            "pe":            str(rec.get("pe",  "0")),
            "pb":            str(rec.get("pb",  "0")),
            "dy":            str(rec.get("dy",  "0")),
            "advances":      int(rec.get("advances",  0) or 0),
            "declines":      int(rec.get("declines",  0) or 0),
            "unchanged":     int(rec.get("unchanged", 0) or 0),
        })

    df = pd.DataFrame(rows)
    print(f"[fetch_all_indices_snapshot] {len(df)} indices fetched "
          f"across {df['key'].nunique() if not df.empty else 0} categories")
    return df


def get_vix_from_snapshot(df: pd.DataFrame) -> float:
    """Extract INDIA VIX last price from a fetch_all_indices_snapshot() DataFrame."""
    if df.empty:
        return 15.0
    mask = df["index"].str.upper() == "INDIA VIX"
    if not mask.any():
        return 15.0
    val = df.loc[mask, "last"].iloc[0]
    return float(val) if val > 0 else 15.0


def get_index_from_snapshot(df: pd.DataFrame, index_name: str) -> dict:
    """
    Look up a single index row from a fetch_all_indices_snapshot() DataFrame.
    index_name is matched case-insensitively against the 'index' column.
    Returns a dict of the row, or {} if not found.
    """
    if df.empty:
        return {}
    mask = df["index"].str.upper() == index_name.strip().upper()
    if not mask.any():
        return {}
    return df.loc[mask].iloc[0].to_dict()


if __name__ == "__main__":
    print("=" * 60)
    print("NSE — allIndices snapshot")
    snap_df = fetch_all_indices_snapshot()
    if not snap_df.empty:
        print(snap_df[["key", "index", "last", "percentChange"]].to_string(index=False))
    print(f"  VIX (from snapshot) = {get_vix_from_snapshot(snap_df)}")
    nifty = get_index_from_snapshot(snap_df, "NIFTY 50")
    print(f"  NIFTY 50 last={nifty.get('last')}  chg={nifty.get('percentChange')}%")

    print("\nNSE — India VIX (via get_unified_market_data)")
    vix, vix_chg, _ = get_unified_market_data()
    print(f"  VIX = {vix}  (chg {vix_chg}%)")

    print("\nNSE — Nifty 50 futures (top 3 rows)")
    fut_df = fetch_nifty_futures("nse50_fut")
    if not fut_df.empty:
        print(fut_df.head(3).to_string(index=False))

    print("\nBSE — SENSEX option chain (top 3 rows)")
    bse_df, bse_spot = fetch_bse_json_options("02 Jul 2026", scrip_cd="1")
    if bse_df is not None:
        print(f"  Spot = {bse_spot}")
        print(bse_df.head(3).to_string(index=False))

    print("\nBSE — SENSEX futures")
    bse_fut = fetch_bse_futures(scrip_cd="1")  # expiry_str auto-computed
    if bse_fut is not None:
        print(bse_fut.to_string(index=False))

    print("=" * 60)