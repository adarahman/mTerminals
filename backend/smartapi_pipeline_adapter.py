"""
smartapi_pipeline_adapter.py
=============================
Reshapes smartapi_client.py's actual data (get_batch_quotes, get_index_quote,
_load_scrip_master, STRIKE_INTERVALS) into the exact wide DataFrame schemas
market_api.py's parse_option_chain_response() / fetch_nifty_futures() produce
— so engine.py, option_chain_json.py, and mTerminals_json.py need zero
downstream changes.

This does NOT reimplement session/auth/token-resolution — all of that stays
in smartapi_client.py via its module-level `_session` singleton. This file
only reshapes and fills two gaps smartapi_client.py's existing helpers don't
cover:
  1. get_atm_chain() drops bid/ask depth + total buy/sell qty from the raw
     FULL-mode quote — needed by mTerminals_json.py's _build_bid_ask_map().
     This adapter pulls straight from get_batch_quotes() instead, keeping
     the raw quote dict, so depth survives.
  2. No FUTIDX (futures) token resolution or VIX token exist in
     smartapi_client.py at all — added here.

Lives in mTerminals/ alongside smartapi_client.py, engine.py, etc.
"""

from datetime import date, datetime
import time

import pandas as pd

from concurrent.futures import ThreadPoolExecutor

from smartapi_client import (
    _load_scrip_master,
    _scrip_indexes,
    get_index_quote,
    get_spot_quote,
    get_batch_quotes,
    get_ltp,
    STRIKE_INTERVALS,
    _round_to_strike,
    _get_strike_interval,
    safe_float,
)
from engine import solve_iv  # your existing Newton-Raphson IV solver

ANNUAL_RISK_FREE_RATE_DEFAULT = 0.07

# Emergency fallback only if the instrument-master resolver is unavailable.
_LOT_SIZES_FALLBACK = {
    "NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120,
    "SENSEX": 20, "BANKEX": 30, "SENSEX50": 75, "PNB": 8000,
}


def _lot_size(underlying: str) -> int:
    """FUTSTK/FUTIDX-derived lot size (shared by futures + all options)."""
    sym = (underlying or "").upper()
    try:
        from smartapi_instruments import get_lot_size
        return get_lot_size(sym)
    except Exception:
        try:
            from option_chain_json import LOT_SIZES
            return LOT_SIZES.get(sym, 65)
        except Exception:
            return _LOT_SIZES_FALLBACK.get(sym, 65)

# ── Expiry format bridge ──────────────────────────────────────────────────
# Rest of the pipeline (option_chain_json.py, engine.py) uses 'DD-Mon-YYYY'
# e.g. '31-Jul-2026'. smartapi_client.py / ScripMaster use 'DDMMMYYYY'
# e.g. '31JUL2026'. Every function below takes/returns the pipeline's
# dash format and converts at the boundary — callers never see SmartAPI's
# format.

def _to_smartapi_expiry(dash_expiry: str) -> str:
    return datetime.strptime(dash_expiry, "%d-%b-%Y").strftime("%d%b%Y").upper()


def _from_smartapi_expiry(smart_expiry: str) -> str:
    return datetime.strptime(smart_expiry, "%d%b%Y").strftime("%d-%b-%Y")


def get_available_expiries(underlying: str, exchange: str = "NFO") -> list[str]:
    """Replacement for the expiryDates list NSE's option-chain-v3 gives for
    free — needed by NEAR/MONTHLY calendar-spread slot resolution."""
    data = _load_scrip_master()
    smart_expiries = sorted(
        {row["expiry"] for row in data
         if row.get("exch_seg") == exchange and row.get("name") == underlying.upper()
         and row.get("instrumenttype") in ("OPTIDX", "OPTSTK") and row.get("expiry")},
        key=lambda d: datetime.strptime(d, "%d%b%Y"),
    )
    today = date.today()
    return [
        _from_smartapi_expiry(e) for e in smart_expiries
        if datetime.strptime(e, "%d%b%Y").date() >= today
    ]


# ── OI change tracking ──────────────────────────────────────────────────
# SmartAPI quotes carry no changeinOpenInterest field, unlike NSE's option
# chain — engine.py's oi_chg_pcr metric (line ~1255) reads CE_ChgOI/PE_ChgOI
# directly, and downstream (oi_analysis.build_master_table_nse) treats it
# as NSE does: cumulative change vs the PREVIOUS DAY'S CLOSE — not vs the
# last poll, and NOT vs whenever this process happened to start.
#
# _day_open_oi is the fixed anchor for the current trading day, keyed per
# (underlying, expiry, strike, side). It is NOT seeded from the first
# SmartAPI tick we happen to see — that would just measure "change since
# this process last restarted," which drifts by a different amount per
# strike depending on restart timing (this is what produced inconsistent
# ChgOI ratios across strikes — each one was really "change since some
# arbitrary restart moment," not "change since previous close").
#
# Instead, the anchor is seeded ONCE per (key, day) from NSE's own
# changeinOpenInterest via market_api.py — anchor = current_NSE_OI -
# NSE_ChgOI, i.e. NSE's own previous-day-close OI for that strike. Every
# SmartAPI tick afterward only adds live movement on top of that correct
# baseline. market_api.py is already used elsewhere in this pipeline (BSE/
# index/futures), so this is a single extra NSE call once per
# underlying+expiry+day, not per poll — not a new dependency.
_day_open_oi: dict[tuple, tuple] = {}  # key -> (date, anchor_oi)

# Per (underlying, expiry_dash, date): {"complete": bool, "last_attempt": float}
# Replaces the old `_seeded_today: set`, which marked a key "done" after a
# single attempt regardless of outcome. That meant any strike/side NSE
# returned with a null CE or PE object (common for illiquid deep OTM/ITM
# contracts — one side often has no live quote while the other does) was
# permanently skipped for the rest of the trading day: it fell back to
# "anchor on first SmartAPI tick" (see _chg_oi below) while every other
# strike/side kept reading a correct previous-day-close-anchored ChgOI.
# Since NSE's per-strike CE/PE data gaps aren't symmetric, this produced a
# real, persistent CE vs PE ChgOI skew rather than a random one.
# Now: only a fully-clean seed (zero skipped strike/sides) is marked
# complete. A partial or failed seed is retried on a cooldown instead of
# never, so a transient NSE gap self-heals within a few polls instead of
# lasting all day.
_SEED_RETRY_COOLDOWN_SEC = 30
_seed_state: dict[tuple, dict] = {}


def _seed_day_anchor_from_nse(underlying: str, expiry_dash: str) -> None:
    """Best-effort: pulls NSE's real changeinOpenInterest and backs out each
    strike/side's true previous-day-close OI to seed _day_open_oi. Retries
    on a cooldown (rather than once per day) until every strike/side NSE
    reports has been seeded, since a partial NSE response (missing CE or PE
    data for some strikes) would otherwise leave those specific strike/sides
    permanently anchored on "first SmartAPI tick" instead of previous close
    — the source of the CE/PE ChgOI ratio inconsistency. If NSE stays
    unreachable (rate-limited/blocked — the whole reason this pipeline runs
    on SmartAPI in the first place) this silently no-ops each attempt and
    callers fall back to the old "anchor on first tick" behavior for any
    key that never got seeded, rather than raising and breaking the live
    feed."""
    today = date.today()
    seed_key = (underlying, expiry_dash, today)
    state = _seed_state.get(seed_key)
    if state and state["complete"]:
        return
    now = time.monotonic()
    if state and (now - state["last_attempt"]) < _SEED_RETRY_COOLDOWN_SEC:
        return  # tried recently and still incomplete — wait out the cooldown
    _seed_state[seed_key] = {"complete": False, "last_attempt": now}

    try:
        # Local import: avoids a hard/circular dependency on market_api.py
        # for callers that never touch NSE (pure BSE symbols), and keeps
        # this network call lazy/on-demand rather than an eager top-level
        # import cost paid by every caller of this module.
        from market_api import fetch_option_chain, parse_option_chain_response
        payload = fetch_option_chain(underlying, expiry_dash)
        df_nse = parse_option_chain_response(payload, expiry_dash)
    except Exception as e:
        print(f"[smartapi_pipeline_adapter] NSE seed fetch failed for "
              f"{underlying} {expiry_dash} ({e}); ChgOI will anchor on "
              f"first SmartAPI tick instead until a seed attempt succeeds "
              f"(retrying every {_SEED_RETRY_COOLDOWN_SEC}s).")
        return

    seeded = 0
    ce_skipped = 0
    pe_skipped = 0
    for row in df_nse.to_dict("records"):
        strike_val = row.get("StrikePrice")
        if strike_val is None:
            continue
        for side, oi_col, chg_col in (("CE", "CE_OI", "CE_ChgOI"), ("PE", "PE_OI", "PE_ChgOI")):
            nse_oi, nse_chg = row.get(oi_col), row.get(chg_col)
            if nse_oi is None or nse_chg is None:
                if side == "CE":
                    ce_skipped += 1
                else:
                    pe_skipped += 1
                continue
            anchor_oi = float(nse_oi) - float(nse_chg)  # NSE's own previous-close OI, in lots
            _day_open_oi[(underlying, expiry_dash, strike_val, side)] = (today, anchor_oi)
            seeded += 1

    total_skipped = ce_skipped + pe_skipped
    _seed_state[seed_key] = {"complete": total_skipped == 0, "last_attempt": now}
    skew_note = ""
    if ce_skipped != pe_skipped:
        skew_note = (f" — CE/PE SKEW: {ce_skipped} CE vs {pe_skipped} PE strikes "
                     f"unseeded, will read anchor-on-first-tick until retried")
    print(f"[smartapi_pipeline_adapter] Seeded ChgOI anchor for "
          f"{underlying} {expiry_dash} from NSE: {seeded} strike/side entries "
          f"({ce_skipped} CE / {pe_skipped} PE skipped){skew_note}")


def _chg_oi(underlying: str, expiry_dash: str, strike: float, side: str, current_oi) -> float:
    key = (underlying, expiry_dash, strike, side)
    cur = float(current_oi or 0.0)
    today = date.today()

    entry = _day_open_oi.get(key)
    if entry is None or entry[0] != today:
        # Not seeded (yet) for today — attempt the NSE seed once for this
        # underlying+expiry, which populates every strike/side in one shot
        # if it succeeds.
        _seed_day_anchor_from_nse(underlying, expiry_dash)
        entry = _day_open_oi.get(key)

    if entry is None or entry[0] != today:
        # NSE seed unavailable and this is genuinely the first time we've
        # seen this key today — fall back to anchoring on the current
        # reading. Reads 0 until the next successful NSE seed corrects it.
        _day_open_oi[key] = (today, cur)
        return 0.0

    anchor_oi = entry[1]
    # `cur` must already be in lots here (same convention as `anchor_oi`,
    # which comes from NSE's raw contract counts) — the caller is
    # responsible for converting SmartAPI's quantity-based opnInterest to
    # lots before calling this. See the ROOT CAUSE note in
    # fetch_option_chain_wide() for why that conversion matters.
    return cur - anchor_oi


# ── Option chain (wide format) ───────────────────────────────────────────

def fetch_option_chain_wide(underlying: str, expiry_dash: str,
                             strikes_around_atm: int = 10, exchange: str = "NFO",
                             r: float = ANNUAL_RISK_FREE_RATE_DEFAULT) -> pd.DataFrame:
    """Direct replacement for
    market_api.parse_option_chain_response(fetch_option_chain(symbol, expiry), expiry).
    Same output columns; source is smartapi_client.py's get_batch_quotes()
    (kept separate from get_atm_chain() specifically to retain depth/qty
    fields get_atm_chain() drops)."""
    expiry_smart = _to_smartapi_expiry(expiry_dash)

    quote = get_spot_quote(underlying)
    if not quote:
        print(f"[smartapi_pipeline_adapter] no spot quote for {underlying}")
        return pd.DataFrame()
    spot = quote["ltp"]

    atm = _round_to_strike(spot, underlying)
    interval = _get_strike_interval(underlying)
    strikes = {atm + (i * interval) for i in range(-strikes_around_atm, strikes_around_atm + 1)}

    # _load_scrip_master() ensures the ScripMaster is loaded and
    # _scrip_indexes is populated (idempotent — no-op if already loaded
    # this process). The actual per-strike lookup below then goes through
    # the pre-built O(1) chain index instead of a raw linear scan over
    # the full ScripMaster list (up to ~165k rows) on every single tick,
    # once per expiry chain requested. That linear scan was the reason
    # per-tick pipeline time rose ~1s after the ScripMaster refresh grew
    # the file ~12% larger — get_atm_chain() already used this same index
    # for exactly this reason; fetch_option_chain_wide() just hadn't been
    # updated to use it.
    _load_scrip_master()
    chain_map = _scrip_indexes["chain"].get((exchange, underlying.upper(), expiry_smart), {})
    strike_lookup = {
        (strike_val, opt_type): info
        for (strike_val, opt_type), info in chain_map.items()
        if strike_val in strikes
    }

    if not strike_lookup:
        print(f"[smartapi_pipeline_adapter] no contracts resolved for {underlying} {expiry_dash}")
        return pd.DataFrame()

    # ── ScripMaster CE/PE parity sanity check ──────────────────────────
    # A healthy option chain has one CE and one PE per strike. If the
    # local ScripMaster cache is stale/incomplete (e.g. a partial/corrupted
    # download, or a save that raced a process restart), one side can go
    # missing for a specific strike while the other survives — the exact
    # failure mode that caused PE OI/LTP/Volume to silently export as 0
    # for one strike while CE at the same strike worked fine. This check
    # surfaces that mismatch immediately instead of requiring a multi-hour
    # debugging session to trace back through the whole pipeline.
    ce_count = sum(1 for (_, side) in strike_lookup if side == "CE")
    pe_count = sum(1 for (_, side) in strike_lookup if side == "PE")
    if ce_count != pe_count:
        print(f"[smartapi_pipeline_adapter] WARNING: CE/PE contract count mismatch for "
              f"{underlying} {expiry_dash} — CE={ce_count} PE={pe_count}. "
              f"ScripMaster cache may be stale/incomplete; consider deleting the local "
              f"cache file to force a fresh download.")

    pairs = [(info["tradingsymbol"], info["token"]) for info in strike_lookup.values()]
    quotes = get_batch_quotes(exchange, pairs, mode="FULL")  # raw dicts, depth included

    dte_years = max((datetime.strptime(expiry_smart, "%d%b%Y").date() - date.today()).days, 1) / 365.0

    by_strike: dict[float, dict] = {}
    for (strike_val, side), info in strike_lookup.items():
        # ── ROOT CAUSE FIX ──
        # Quotes must be keyed/looked-up by TOKEN, not by tradingsymbol
        # string. get_batch_quotes() stores each quote under the live
        # API response's own tradingSymbol field, while `info["tradingsymbol"]`
        # here comes from the ScripMaster file's `symbol` field — two
        # different sources of truth for the same string, with no
        # normalization between them. Token is the exchange-canonical,
        # collision-proof identifier and is present on both sides, so
        # keying on it removes the mismatch risk entirely.
        q = quotes.get(str(info["token"]))
        if not q:
            continue

        rec = by_strike.setdefault(strike_val, {
            "StrikePrice": strike_val, "Expiry": expiry_dash, "Spot": spot, "Symbol": underlying,
        })

        # ── ROOT CAUSE FIX ──
        # SmartAPI's opnInterest is reported in actual quantity (shares),
        # not lots — unlike NSE's openInterest/changeinOpenInterest, which
        # market_api.py passes through raw as lot (contract) counts, and
        # which oi_analysis.build_master_table_nse() assumes for BOTH
        # CE_OI and CE_ChgOI when it later multiplies by lot_size once to
        # get to quantity terms. Without this conversion, `oi_now` (already
        # in quantity) was subtracted directly against `anchor_oi` (still
        # in lots) inside _chg_oi(), so CE_ChgOI came out ~lot_size times
        # too large — the OI table's OI vs ChgOI mismatch. Converting to
        # lots here keeps CE_OI/CE_ChgOI on the same convention as the NSE
        # path for every downstream consumer.
        lot_size = _lot_size(underlying)
        oi_now = (q.get("opnInterest") or 0) / lot_size
        chg_oi = _chg_oi(underlying, expiry_dash, strike_val, side, oi_now)
        prev_oi = float(oi_now or 0.0) - chg_oi

        ltp = safe_float(q.get("ltp"))
        depth = q.get("depth") or {}
        buy0 = (depth.get("buy") or [{}])[0]
        sell0 = (depth.get("sell") or [{}])[0]

        iv = (solve_iv(ltp, spot, strike_val, dte_years, r,
                        opt_type="C" if side == "CE" else "P") * 100.0
              if spot and ltp else 0.0)

        rec[f"{side}_OI"] = oi_now
        rec[f"{side}_ChgOI"] = chg_oi
        rec[f"{side}_PctChgOI"] = round((chg_oi / prev_oi) * 100.0, 2) if prev_oi > 0 else 0.0
        rec[f"{side}_Volume"] = q.get("tradeVolume")
        rec[f"{side}_IV"] = round(iv, 2)
        rec[f"{side}_LTP"] = ltp
        rec[f"{side}_Change"] = q.get("netChange")
        rec[f"{side}_pChange"] = q.get("percentChange")
        rec[f"{side}_BidQty"] = buy0.get("quantity")
        rec[f"{side}_BidPrice"] = buy0.get("price")
        rec[f"{side}_AskQty"] = sell0.get("quantity")
        rec[f"{side}_AskPrice"] = sell0.get("price")
        rec[f"{side}_BuyQty"] = q.get("totBuyQuan")
        rec[f"{side}_SellQty"] = q.get("totSellQuan")

    return pd.DataFrame(list(by_strike.values())).sort_values("StrikePrice").reset_index(drop=True)


# ── Futures ───────────────────────────────────────────────────────────────
# Gap #2: smartapi_client.py resolves OPTIDX/OPTSTK tokens (find_option_token)
# but has no FUTIDX path. Minimal addition here rather than touching that
# file — same _load_scrip_master() cache, no new network dependency.

def _get_futures_contract(underlying: str, expiry_dash: str | None = None,
                           exchange: str = "NFO") -> dict | None:
    data = _load_scrip_master()
    cands = [row for row in data
             if row.get("exch_seg") == exchange
             and row.get("name") == underlying.upper()
             and row.get("instrumenttype") == "FUTIDX"]
    if not cands:
        return None
    cands.sort(key=lambda r: datetime.strptime(r["expiry"], "%d%b%Y"))
    if expiry_dash:
        target = _to_smartapi_expiry(expiry_dash)
        cands = [c for c in cands if c["expiry"] == target]
        if not cands:
            return None
    return cands[0]  # nearest expiry if not specified


def fetch_futures_wide(underlying: str, expiry_dash: str | None = None,
                        exchange: str = "NFO") -> pd.DataFrame:
    """Replacement for market_api.fetch_nifty_futures()."""
    fut = _get_futures_contract(underlying, expiry_dash, exchange)
    if not fut:
        return pd.DataFrame()

    quotes = get_batch_quotes(exchange, [(fut["symbol"], fut["token"])], mode="FULL")
    q = quotes.get(str(fut["token"]))
    if not q:
        return pd.DataFrame()

    spot_quote = get_spot_quote(underlying)
    spot = spot_quote["ltp"] if spot_quote else 0.0
    ltp = safe_float(q.get("ltp"))

    return pd.DataFrame([{
        "Contract": fut["symbol"],
        "Underlying": underlying,
        "Expiry": _from_smartapi_expiry(fut["expiry"]),
        "LTP": ltp,
        "Change": q.get("netChange"),
        "PctChange": q.get("percentChange"),
        "Open": q.get("open"),
        "High": q.get("high"),
        "Low": q.get("low"),
        "PrevClose": q.get("close"),
        "Volume": q.get("tradeVolume"),
        # SmartAPI's quote has no turnover field (unlike NSE's totalTurnover)
        # — left None rather than a fabricated estimate; if a per-contract
        # VWAP downstream needs this, compute it from historical candles
        # (smartapi_history.py's get_candle_data) instead of guessing here.
        "Turnover": None,
        "OI": q.get("opnInterest"),
        "Spot": spot,
        "Basis": round(ltp - spot, 2) if spot else None,
    }])


# ── VIX ──────────────────────────────────────────────────────────────────
# Gap #2 continued: not in smartapi_client.py's INDEX_TOKENS. Verified
# directly against the live scrip master (2026-07-14):
#   token=99926017, tradingsymbol="India VIX", exch_seg=NSE
_VIX_TRADINGSYMBOL = "India VIX"
_VIX_TOKEN = "99926017"
_TICKER_SYMBOLS = ["NIFTY", "BANKNIFTY", "MIDCPNIFTY", "FINNIFTY"]

# Cache for the batched fetch each tick — populated once by
# fetch_all_pills_and_vix_batched(), then read by the three thin wrapper
# functions below so existing callers (ThreadPoolExecutor submissions in
# option_chain_json.py) don't need to change at all.
_BATCH_CACHE: dict = {}


def fetch_all_pills_and_vix_batched():
    """Replaces 6 separate ltpData calls (each throttled at 1.0s globally)
    with 2 batched getMarketData calls (0.35s each) — NIFTY/BANKNIFTY/
    MIDCPNIFTY/FINNIFTY/India VIX on NSE in one call, SENSEX on BSE in a
    second call. Was costing ~6s/tick in pure rate-limit wait; now ~0.7s.
    Populates _BATCH_CACHE; call this ONCE per tick before the three
    wrapper functions below."""
    from smartapi_client import INDEX_TOKENS, get_batch_quotes

    nse_pairs = [
        (sym, INDEX_TOKENS[sym]["token"])
        for sym in _TICKER_SYMBOLS
        if sym in INDEX_TOKENS
    ]
    nse_pairs.append((_VIX_TRADINGSYMBOL, _VIX_TOKEN))

    nse_by_token = get_batch_quotes("NSE", nse_pairs, mode="FULL")

    sensex_info = INDEX_TOKENS.get("SENSEX")
    bse_pairs = [("SENSEX", sensex_info["token"])] if sensex_info else []
    bse_by_token = get_batch_quotes("BSE", bse_pairs, mode="FULL") if bse_pairs else {}

    # get_batch_quotes keys its return dict by token (str(symbolToken)),
    # not by the tradingsymbol string we requested with — re-key here by
    # symbol so the three wrapper functions below (which look up by
    # symbol name, e.g. "India VIX", "NIFTY", "SENSEX") can actually find
    # their entries. Without this, every lookup silently misses: VIX logs
    # it, the ticker-strip wrappers just return None with no warning.
    _BATCH_CACHE.clear()
    for sym, token in nse_pairs + bse_pairs:
        row = nse_by_token.get(str(token)) or bse_by_token.get(str(token))
        if row:
            _BATCH_CACHE[sym] = row


def fetch_vix_smartapi() -> tuple[float | None, float]:
    """Now reads from _BATCH_CACHE (populated by
    fetch_all_pills_and_vix_batched()) instead of its own ltpData call."""
    d = _BATCH_CACHE.get(_VIX_TRADINGSYMBOL)
    if not d:
        print("[smartapi_pipeline_adapter] VIX missing from batch cache")
        return None, 0.0
    ltp = safe_float(d.get("ltp"))
    close = safe_float(d.get("close"))
    chg_pct = round((ltp - close) / close * 100.0, 2) if close else 0.0
    return (ltp if ltp else None), chg_pct


def _index_quote_to_ticker_entry(symbol: str, quote: dict | None) -> dict | None:
    if not quote:
        return None
    ltp, close = quote.get("ltp"), quote.get("close")
    change = round(ltp - close, 2) if (ltp is not None and close) else 0.0
    pct = round((change / close) * 100.0, 2) if close else 0.0
    return {
        "Symbol":        symbol,
        "BackendSymbol": symbol,
        "Last Price":    ltp,
        "% Change":      pct,
        "Change":        change,
        "Prev Close":    close,
    }


def fetch_ticker_payload_smartapi(symbols=None) -> list:
    """Now reads from _BATCH_CACHE instead of firing one ltpData call per
    symbol via ThreadPoolExecutor."""
    symbols = symbols or _TICKER_SYMBOLS
    payload = []
    for sym in symbols:
        d = _BATCH_CACHE.get(sym)
        entry = _index_quote_to_ticker_entry(sym, {
            "ltp": safe_float(d.get("ltp")),
            "close": safe_float(d.get("close")),
        }) if d else None
        if entry:
            payload.append(entry)
    return payload


def fetch_sensex_ticker_smartapi():
    d = _BATCH_CACHE.get("SENSEX")
    quote = {
        "ltp": safe_float(d.get("ltp")),
        "close": safe_float(d.get("close")),
    } if d else None
    return _index_quote_to_ticker_entry("SENSEX", quote)