"""Canonical broker-neutral REST market-data pipeline.
Reshapes the REST quote client and instrument master into the exact wide
DataFrame schemas market_api.py's parse_option_chain_response() /
fetch_nifty_futures() produce — so engine.py, option_chain_json.py, and
mTerminals_json.py need zero downstream changes.

This does NOT reimplement session/auth/token-resolution — all of that stays
in the broker REST client via its module-level session singleton. This file
only reshapes and fills two gaps the client's existing helpers don't cover:
  1. get_atm_chain() drops bid/ask depth + total buy/sell qty from the raw
     FULL-mode quote — needed by mTerminals_json.py's _build_bid_ask_map().
     This adapter pulls straight from get_batch_quotes() instead, keeping
     the raw quote dict, so depth survives.
  2. No FUTIDX (futures) token resolution or VIX token exist in the base
     REST client helpers at all — added here.

The current implementation still relies on a few SmartAPI instrument-master
helpers for shared symbol and lot metadata. Provider routing itself goes only
through :mod:`brokers.market_data`.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pandas as pd

from brokers.market_data import market_data

try:
    from config import settings as _md_settings
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from backend.config import settings as _md_settings
from brokers.smartapi_client import (
    STRIKE_INTERVALS,
    _canonical_underlying,
    _get_strike_interval,
    # NOTE: _load_scrip_master/_round_to_strike/_get_strike_interval are
    # SmartAPI-private (underscore) internals, reached into directly here
    # rather than through MarketData — that's a separate leaky-abstraction
    # issue (they're generic option math, not really broker-specific) and
    # is out of scope for this pass.
    _load_scrip_master,
    _round_to_strike,
    safe_float,
)
from oi.pricing import solve_iv  # your existing Newton-Raphson IV solver
from storage.caches import TickScopedDict

logger = logging.getLogger(__name__)

ANNUAL_RISK_FREE_RATE_DEFAULT = 0.07

# Emergency fallback only if the instrument-master resolver is unavailable.
_LOT_SIZES_FALLBACK = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "FINNIFTY": 60,
    "MIDCPNIFTY": 120,
    "SENSEX": 20,
    "BANKEX": 30,
    "SENSEX50": 75,
    "PNB": 8000,
}


def _lot_size(underlying: str) -> int:
    """FUTSTK/FUTIDX-derived lot size (shared by futures + all options)."""
    sym = (underlying or "").upper()
    try:
        from brokers.smartapi_instruments import get_lot_size

        return get_lot_size(sym)
    except Exception:
        try:
            from lot_sizes import LOT_SIZES

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


def _canon_underlying(underlying: str) -> str:
    """Upper-case a requested underlying, mapped to the ScripMaster's exact
    name when a full company name was typed ("ZYDUS LIFESCIENCES LTD" ->
    "ZYDUSLIFE"). Falls back to the raw request on ambiguity/None — the
    downstream scan then simply finds no rows and fails loudly."""
    u = (underlying or "").strip().upper()
    if not u:
        return u
    return _canonical_underlying(u) or u


def get_available_expiries(underlying: str, exchange: str = "NFO") -> list[str]:
    """Replacement for the expiryDates list NSE's option-chain-v3 gives for
    free — needed by NEAR/MONTHLY calendar-spread slot resolution."""
    data = _load_scrip_master()
    name_u = _canon_underlying(underlying)
    smart_expiries = sorted(
        {
            row["expiry"]
            for row in data
            if row.get("exch_seg") == exchange
            and row.get("name") == name_u
            and row.get("instrumenttype") in ("OPTIDX", "OPTSTK")
            and row.get("expiry")
        },
        key=lambda d: datetime.strptime(d, "%d%b%Y"),
    )
    today = date.today()
    return [
        _from_smartapi_expiry(e)
        for e in smart_expiries
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
        logger.warning(
            f"NSE seed fetch failed for "
            f"{underlying} {expiry_dash} ({e}); ChgOI will anchor on "
            f"first SmartAPI tick instead until a seed attempt succeeds "
            f"(retrying every {_SEED_RETRY_COOLDOWN_SEC}s)."
        )
        return

    seeded = 0
    ce_skipped = 0
    pe_skipped = 0
    for row in df_nse.to_dict("records"):
        strike_val = row.get("StrikePrice")
        if strike_val is None:
            continue
        for side, oi_col, chg_col in (
            ("CE", "CE_OI", "CE_ChgOI"),
            ("PE", "PE_OI", "PE_ChgOI"),
        ):
            nse_oi, nse_chg = row.get(oi_col), row.get(chg_col)
            if nse_oi is None or nse_chg is None:
                if side == "CE":
                    ce_skipped += 1
                else:
                    pe_skipped += 1
                continue
            anchor_oi = float(nse_oi) - float(
                nse_chg
            )  # NSE's own previous-close OI, in lots
            _day_open_oi[(underlying, expiry_dash, strike_val, side)] = (
                today,
                anchor_oi,
            )
            seeded += 1

    total_skipped = ce_skipped + pe_skipped
    _seed_state[seed_key] = {"complete": total_skipped == 0, "last_attempt": now}
    skew_note = ""
    if ce_skipped != pe_skipped:
        skew_note = (
            f" — CE/PE SKEW: {ce_skipped} CE vs {pe_skipped} PE strikes "
            f"unseeded, will read anchor-on-first-tick until retried"
        )
    logger.info(
        f"Seeded ChgOI anchor for "
        f"{underlying} {expiry_dash} from NSE: {seeded} strike/side entries "
        f"({ce_skipped} CE / {pe_skipped} PE skipped){skew_note}"
    )


def _chg_oi(
    underlying: str, expiry_dash: str, strike: float, side: str, current_oi
) -> float:
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


def fetch_option_chain_wide(
    underlying: str,
    expiry_dash: str,
    strikes_around_atm: int = 10,
    exchange: str = "NFO",
    r: float = ANNUAL_RISK_FREE_RATE_DEFAULT,
) -> pd.DataFrame:
    """Direct replacement for
    market_api.parse_option_chain_response(fetch_option_chain(symbol, expiry), expiry).
    Same output columns; source is smartapi_client.py's get_batch_quotes()
    (kept separate from get_atm_chain() specifically to retain depth/qty
    fields get_atm_chain() drops).

    The source broker is the RUNTIME-active provider (see
    brokers.market_data.get_active_provider() — switchable via the
    Dashboard's DATA SOURCE picker without a restart), not the frozen
    config value. Upstox/Shoonya/Breeze return their whole ATM chain from
    one get_atm_chain() call (rows already carry ltp/oi/volume); Kite's
    get_atm_chain() returns instrument metadata only, so this adds one
    get_batch_quotes() pass to pull live quotes onto those rows."""
    expiry_smart = _to_smartapi_expiry(expiry_dash)

    # Canonicalize once at the entry point: _chg_oi()/_seed_day_anchor_from_nse()
    # key _day_open_oi by `underlying` and fetch NSE's option-chain API (which
    # only accepts the exchange TICKER), so a full-company-name request
    # ("ADANI ENERGY SOLUTION LTD") must be mapped to "ADANIENSOL" here or the
    # previous-close OI anchor never seeds and ChgOI degenerates to an abrupt
    # first-tick delta. Idempotent for inputs that are already canonical
    # tickers. Every provider below accepts the canonical form internally.
    underlying = _canon_underlying(underlying)

    from brokers.market_data import get_active_provider

    provider = get_active_provider()

    if provider in ("UPSTOX", "SHOONYA", "BREEZE", "KITE", "KOTAK"):
        chain = None
        chain_error = None
        try:
            chain = market_data.get_atm_chain(
                underlying,
                expiry_dash,
                strikes_around_atm=strikes_around_atm,
                exchange=exchange,
            )
        except Exception as exc:
            chain_error = exc
        if not chain:
            # A broker auth/network failure (login down, session dead) must
            # not empty the whole chain: fall back to the public NSE/BSE
            # source. Its rows already arrive in the DataFrame's LOTS
            # convention, so the per-provider OI normalization below must
            # NOT re-run — `provider` is reassigned accordingly.
            logger.warning(
                f"[{provider}] no option chain for {underlying} {expiry_dash} "
                f"({chain_error or 'empty'}) — falling back to public NSE/BSE"
            )
            try:
                from brokers.market_data import NseBseMarketData

                chain = NseBseMarketData().get_atm_chain(
                    underlying,
                    expiry_dash,
                    strikes_around_atm=strikes_around_atm,
                    exchange=exchange,
                )
                provider = "NSE_BSE"
            except Exception as fallback_exc:
                logger.warning(
                    f"[nse_bse] fallback chain also failed for {underlying} "
                    f"{expiry_dash}: {fallback_exc}"
                )
                return pd.DataFrame()
        if not chain:
            logger.warning(f"no {provider.title()} option chain for {underlying} {expiry_dash}")
            return pd.DataFrame()
        spot = float(chain.get("spot") or 0.0)
        rows = chain.get("rows") or []
        if not rows:
            return pd.DataFrame()

        if provider == "KITE":
            # Kite's get_atm_chain() returns instrument metadata only
            # (strike/type/token/tradingsymbol) — overlay live quotes via one
            # get_batch_quotes() call so the normalized rows below carry
            # ltp/oi/volume like the other providers' get_atm_chain() does.
            pairs = [
                (row.get("tradingsymbol"), row.get("token"))
                for row in rows
                if row.get("tradingsymbol")
            ]
            try:
                quotes = market_data.get_batch_quotes(exchange, pairs, mode="FULL") or {}
            except Exception as exc:
                logger.warning(
                    f"[kite] batch quote overlay failed for {underlying} {expiry_dash}: {exc}"
                )
                quotes = {}
            for row in rows:
                q = quotes.get(row.get("tradingsymbol")) or {}
                if not q:
                    continue
                row["ltp"] = q.get("last_price")
                row["oi"] = q.get("oi")
                row["volume"] = q.get("volume")
                row["net_change"] = q.get("net_change")
                row["pct_change"] = q.get("percent_change")

        try:
            expiry_dt = datetime.strptime(expiry_dash, "%d-%b-%Y").date()
        except (TypeError, ValueError):
            try:
                expiry_dt = datetime.strptime(expiry_smart, "%d%b%Y").date()
            except (TypeError, ValueError):
                expiry_dt = date.today()
        dte_years = max((expiry_dt - date.today()).days, 1) / 365.0

        by_strike: dict[float, dict] = {}
        for row in rows:
            try:
                strike_val = float(row.get("strike"))
            except (TypeError, ValueError):
                continue
            side = row.get("type")
            if side not in ("CE", "PE"):
                continue
            rec = by_strike.setdefault(
                strike_val,
                {
                    "StrikePrice": strike_val,
                    "Expiry": expiry_dash,
                    "Spot": spot,
                    "Symbol": underlying,
                },
            )
            ltp = safe_float(row.get("ltp"))
            oi_now = safe_float(row.get("oi"))
            # OI unit normalization — the DataFrame/_chg_oi() convention is
            # LOTS (contracts), same as NSE's anchor: build_master_table_nse()
            # re-multiplies by lot_size downstream to quantity for display.
            # Upstox/Kite/Shoonya/Breeze report OI in QUANTITY (shares) like
            # SmartAPI's opnInterest (see the ROOT CAUSE note in the SmartAPI
            # branch below; Breeze's open_interest is likewise raw share
            # counts — ICICI's own SDK docs show a 2435175 OI on a NIFTY
            # 23200 CE, i.e. ~32469 lots at lot_size 75) — feeding raw share
            # counts through would make CE_OI/PE_OI lot_size× too large AND,
            # mixed against NSE's lot anchor inside _chg_oi(), turn ChgOI
            # into garbage. Convert to lots using the broker's own lot_size
            # when the row carries it. Kotak's quotes() open_interest is
            # treated the same way pending live verification in the Kotak
            # smoke test (see kotak_market_data.py's docstring caveats).
            if provider in ("UPSTOX", "SHOONYA", "KITE", "BREEZE", "KOTAK"):
                lot_size = row.get("lot_size") or _lot_size(underlying)
                if lot_size:
                    oi_now = oi_now / lot_size
            chg_oi = _chg_oi(underlying, expiry_dash, strike_val, side, oi_now)
            prev_oi = float(oi_now or 0.0) - chg_oi
            rec[f"{side}_OI"] = oi_now
            rec[f"{side}_ChgOI"] = chg_oi
            rec[f"{side}_PctChgOI"] = (
                round((chg_oi / prev_oi) * 100.0, 2) if prev_oi > 0 else 0.0
            )
            rec[f"{side}_Volume"] = row.get("volume")
            rec[f"{side}_IV"] = (
                round(
                    solve_iv(
                        ltp,
                        spot,
                        strike_val,
                        dte_years,
                        r,
                        opt_type="C" if side == "CE" else "P",
                    )
                    * 100.0,
                    2,
                )
                if spot and ltp
                else 0.0
            )
            # Snapshot providers (including Kotak Neo) already expose the
            # option's day change / previous close with their live quote.
            # This used to be discarded by assigning None below, so the UI
            # rendered every CE/PE LTP change as zero despite a fresh LTP.
            # Prefer the provider's signed change; derive it from close only
            # when the broker omits that field.
            raw_change = row.get("net_change")
            previous_close = safe_float(row.get("close"))
            change = (
                safe_float(raw_change)
                if raw_change is not None
                else round(ltp - previous_close, 2)
                if previous_close
                else 0.0
            )
            raw_pct_change = row.get("pct_change")
            pct_change = (
                safe_float(raw_pct_change)
                if raw_pct_change is not None
                else round((change / previous_close) * 100.0, 2)
                if previous_close
                else 0.0
            )
            rec[f"{side}_LTP"] = ltp
            rec[f"{side}_Change"] = change
            rec[f"{side}_pChange"] = pct_change
            rec[f"{side}_BidQty"] = None
            rec[f"{side}_BidPrice"] = None
            rec[f"{side}_AskQty"] = None
            rec[f"{side}_AskPrice"] = None
            rec[f"{side}_BuyQty"] = None
            rec[f"{side}_SellQty"] = None

        return (
            pd.DataFrame(list(by_strike.values()))
            .sort_values("StrikePrice")
            .reset_index(drop=True)
        )

    quote = market_data.get_spot_quote(underlying)
    if not quote:
        logger.warning(f"no spot quote for {underlying}")
        return pd.DataFrame()
    spot = quote["ltp"]

    atm = _round_to_strike(spot, underlying)
    interval = _get_strike_interval(underlying)
    strikes = {
        atm + (i * interval) for i in range(-strikes_around_atm, strikes_around_atm + 1)
    }

    data = _load_scrip_master()
    name_u = _canon_underlying(underlying)
    strike_lookup = {}
    for row in data:
        if not (
            row.get("exch_seg") == exchange
            and row.get("name") == name_u
            and row.get("expiry") == expiry_smart
        ):
            continue
        try:
            strike_val = int(round(float(row["strike"]) / 100))
        except (KeyError, ValueError, TypeError):
            continue
        symbol = row.get("symbol", "")
        opt_type = (
            "CE" if symbol.endswith("CE") else "PE" if symbol.endswith("PE") else None
        )
        if opt_type and strike_val in strikes:
            strike_lookup[(strike_val, opt_type)] = {
                "token": row["token"],
                "tradingsymbol": symbol,
            }

        if opt_type and strike_val in strikes:
            strike_lookup[(strike_val, opt_type)] = {
                "token": row["token"],
                "tradingsymbol": symbol,
            }

    pairs = [(info["tradingsymbol"], info["token"]) for info in strike_lookup.values()]
    quotes = market_data.get_batch_quotes(
        exchange, pairs, mode="FULL"
    )  # raw dicts, depth included

    dte_years = (
        max((datetime.strptime(expiry_smart, "%d%b%Y").date() - date.today()).days, 1)
        / 365.0
    )

    by_strike: dict[float, dict] = {}
    for (strike_val, side), info in strike_lookup.items():
        q = quotes.get(info["tradingsymbol"])
        if not q:
            continue

        rec = by_strike.setdefault(
            strike_val,
            {
                "StrikePrice": strike_val,
                "Expiry": expiry_dash,
                "Spot": spot,
                "Symbol": underlying,
            },
        )

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

        iv = (
            solve_iv(
                ltp,
                spot,
                strike_val,
                dte_years,
                r,
                opt_type="C" if side == "CE" else "P",
            )
            * 100.0
            if spot and ltp
            else 0.0
        )

        rec[f"{side}_OI"] = oi_now
        rec[f"{side}_ChgOI"] = chg_oi
        rec[f"{side}_PctChgOI"] = (
            round((chg_oi / prev_oi) * 100.0, 2) if prev_oi > 0 else 0.0
        )
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

    if not by_strike:
        sample = (
            list(quotes.keys())[:5]
            if isinstance(quotes, dict)
            else f"<{type(quotes).__name__}>"
        )
        logger.warning(
            f"0/{len(strike_lookup)} resolved contracts got quotes for "
            f"{underlying} {expiry_dash} (exchange={exchange}) — "
            f"returning empty chain frame. quotes keys sample={sample!r}"
        )
        return pd.DataFrame()

    return (
        pd.DataFrame(list(by_strike.values()))
        .sort_values("StrikePrice")
        .reset_index(drop=True)
    )


# ── Futures ───────────────────────────────────────────────────────────────
# Gap #2: smartapi_client.py resolves OPTIDX/OPTSTK tokens (find_option_token)
# but has no FUTIDX path. Minimal addition here rather than touching that
# file — same _load_scrip_master() cache, no new network dependency.


def _get_futures_contract(
    underlying: str,
    expiry_dash: str | None = None,
    exchange: str = "NFO",
    which: str = "NEAR",
) -> dict | None:
    """which is only consulted when expiry_dash is None (i.e. "give me
    a monthly slot by relative position" instead of "give me this exact
    date") — NEAR/NEXT/FAR map to the 1st/2nd/3rd soonest listed FUTIDX
    expiry. Clamped to whatever's actually listed (FAR silently becomes
    NEXT or NEAR if a 3rd month isn't listed yet) rather than returning
    None for an out-of-range request — a caller asking for "the far
    month" shouldn't get nothing just because NSE hasn't listed it yet.

    instrumenttype covers BOTH "FUTIDX" (index futures — NIFTY/BANKNIFTY)
    and "FUTSTK" (single-stock futures — RELIANCE/etc.) — reusing
    smartapi_instruments._FNO_FUT_TYPES rather than a separate literal,
    since that's the same set get_lot_size() and the F&O lot-size table
    already treat as "this is a futures contract" elsewhere in this
    codebase. Previously filtered to "FUTIDX" only, so this always
    silently returned None (empty futures fetch, PRICE_SOURCE=FUT
    quietly no-op'd back to EQ) for any single-stock underlying.

    Rows with an unparseable `expiry` are skipped rather than raising —
    one malformed row in a ~160k-row master shouldn't take down futures
    resolution for every underlying.

    For the relative-position (`which`) path only, already-expired
    contracts are dropped before indexing. _load_scrip_master() has a
    stale-cache fallback (network failure -> reuse yesterday's file on
    disk) — without this filter, hitting that fallback on or after a
    contract's expiry date would resolve NEAR to a dead contract instead
    of rolling to what should now be NEAR. The exact-date path
    (expiry_dash set) is left unfiltered: nothing in this codebase calls
    it with a value today, but a future caller doing a historical/backtest
    lookup should still be able to ask for a specific past expiry."""
    from brokers.smartapi_instruments import _FNO_FUT_TYPES

    def _parse_expiry(row):
        try:
            return datetime.strptime(row["expiry"], "%d%b%Y")
        except (KeyError, ValueError, TypeError):
            return None

    data = _load_scrip_master()
    name_u = _canon_underlying(underlying)
    cands = [
        row
        for row in data
        if row.get("exch_seg") == exchange
        and row.get("name") == name_u
        and row.get("instrumenttype") in _FNO_FUT_TYPES
    ]
    cands = [(row, _parse_expiry(row)) for row in cands]
    cands = [(row, exp) for row, exp in cands if exp is not None]
    if not cands:
        return None
    cands.sort(key=lambda pair: pair[1])
    if expiry_dash:
        target = _to_smartapi_expiry(expiry_dash)
        matches = [row for row, _exp in cands if row["expiry"] == target]
        if not matches:
            return None
        return matches[0]
    today = datetime.combine(date.today(), datetime.min.time())
    live = [(row, exp) for row, exp in cands if exp >= today] or cands
    idx = {"NEAR": 0, "NEXT": 1, "FAR": 2}.get(which, 0)
    idx = min(idx, len(live) - 1)
    return live[idx][0]


def fetch_futures_wide(
    underlying: str,
    expiry_dash: str | None = None,
    exchange: str = "NFO",
    which: str = "NEAR",
) -> pd.DataFrame:
    """Replacement for market_api.fetch_nifty_futures().

    IMPORTANT: expiry_dash means "this exact futures expiry date" — do
    NOT pass the options chain's EXPIRY here (previously done at both
    option_chain_json.py call sites). NIFTY/BANKNIFTY futures are listed
    monthly; options are often weekly. Passing a weekly options expiry
    as expiry_dash makes _get_futures_contract()'s exact-match filter
    empty on every week that isn't the monthly expiry week, silently
    returning an empty DataFrame the rest of the month. Leave expiry_dash
    None and use `which` (NEAR/NEXT/FAR) to pick a monthly slot by
    relative position instead — see option_chain_json.py's FUTURES_EXPIRY.

    Routing: the `which`-based path (the only one any real caller uses —
    grep-verified against option_chain_json.py/server/bridge.py) goes
    through MarketData.get_futures_quote(), the shared, provider-neutral
    FUT abstraction in brokers/market_data.py — see that module for the
    per-provider resolution/fallback rules (which providers have native
    FUTIDX resolution vs. fall back to the NSE/BSE public API, and how
    that fallback is flagged via the quote's "FutSource" field rather
    than being silent).

    The exact-date (expiry_dash) path only ever worked for SmartAPI/
    Upstox even before this refactor — Kotak/Shoonya/Kite/Breeze/NSE_BSE
    all silently ignored expiry_dash and used `which` instead. No current
    caller passes expiry_dash at all, so this is kept as a narrow legacy
    path (SmartAPI/Upstox only) rather than migrated into the shared
    abstraction, which doesn't take an exact-date parameter.
    """
    if expiry_dash:
        from brokers.market_data import get_active_provider

        provider = get_active_provider()
        if provider == "SMARTAPI":
            return _fetch_futures_exact_date_smartapi(underlying, expiry_dash, exchange)
        if provider == "UPSTOX":
            return _fetch_futures_exact_date_upstox(underlying, expiry_dash, exchange)
        logger.warning(
            "[fetch_futures_wide] expiry_dash=%r requested but %s has no exact-date "
            "FUT resolution — falling back to which=%r instead of silently ignoring it",
            expiry_dash,
            provider,
            which,
        )

    quote = market_data.get_futures_quote(underlying, which=which)
    if not quote:
        return pd.DataFrame()
    return pd.DataFrame([quote])


def _fetch_futures_exact_date_upstox(
    underlying: str, expiry_dash: str, exchange: str = "NFO"
) -> pd.DataFrame:
    """Legacy exact-date FUT lookup for Upstox. No real caller today
    (see fetch_futures_wide's docstring) — kept only so a future exact-
    date caller doesn't silently regress to which-based resolution."""
    from brokers.upstox_client import _load_instrument_dump

    scope = "BSE" if exchange.upper() in ("BFO", "BSE") else "NSE"
    data = _load_instrument_dump(scope)
    underlying_u = underlying.upper()
    from brokers.upstox_client import _canonical_name as _up_canonical

    name_u = _up_canonical(underlying, data) or underlying_u

    def _parse_expiry(row):
        raw = row.get("expiry")
        if raw in (None, "", 0):
            return None
        if isinstance(raw, (int, float)):
            try:
                return datetime.utcfromtimestamp(raw / 1000)
            except (OverflowError, OSError, ValueError):
                return None
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d")
        except ValueError:
            try:
                return datetime.strptime(str(raw), "%d-%b-%Y")
            except ValueError:
                try:
                    return datetime.strptime(str(raw), "%d%b%Y")
                except ValueError:
                    return None

    cands = [
        row
        for row in data
        if row.get("instrument_type") == "FUT"
        and (row.get("name") or "").upper() == name_u
    ]
    cands = [(row, _parse_expiry(row)) for row in cands]
    cands = [(row, exp) for row, exp in cands if exp is not None]
    if not cands:
        return pd.DataFrame()
    cands.sort(key=lambda pair: pair[1])

    try:
        target = datetime.strptime(expiry_dash, "%d-%b-%Y")
    except ValueError:
        try:
            target = datetime.strptime(expiry_dash, "%d%b%Y")
        except ValueError:
            try:
                target = datetime.strptime(expiry_dash, "%Y-%m-%d")
            except ValueError:
                return pd.DataFrame()
    matches = [row for row, exp in cands if exp.date() == target.date()]
    if not matches:
        return pd.DataFrame()
    fut = matches[0]

    quotes = market_data.get_batch_quotes(
        exchange,
        [(fut.get("trading_symbol"), fut.get("instrument_key"))],
        mode="FULL",
    )
    q = quotes.get(fut.get("trading_symbol")) if quotes else None
    if not q:
        return pd.DataFrame()

    spot_quote = market_data.get_spot_quote(underlying)
    spot = spot_quote["ltp"] if spot_quote else 0.0
    ltp = safe_float(q.get("last_price"))
    prev_close = safe_float(q.get("close"))
    change = q.get("net_change")
    pct = q.get("percent_change")
    if pct is None and prev_close:
        pct = round(((ltp - prev_close) / prev_close) * 100.0, 2)

    exp_raw = fut.get("expiry")
    if isinstance(exp_raw, (int, float)):
        exp_str = datetime.utcfromtimestamp(exp_raw / 1000).strftime("%d-%b-%Y")
    else:
        exp_str = str(exp_raw)

    return pd.DataFrame(
        [
            {
                "Contract": fut.get("trading_symbol"),
                "Underlying": underlying,
                "Expiry": exp_str,
                "LTP": ltp,
                "Change": change,
                "PctChange": pct,
                "Open": q.get("open"),
                "High": q.get("high"),
                "Low": q.get("low"),
                "PrevClose": prev_close,
                "Volume": q.get("volume"),
                "Turnover": None,
                "OI": q.get("oi"),
                "Spot": spot,
                "Basis": round(ltp - spot, 2) if spot else None,
            }
        ]
    )


def _fetch_futures_exact_date_smartapi(
    underlying: str, expiry_dash: str, exchange: str = "NFO"
) -> pd.DataFrame:
    """Legacy exact-date FUT lookup for SmartAPI. No real caller today
    (see fetch_futures_wide's docstring) — kept only so a future exact-
    date caller doesn't silently regress to which-based resolution."""
    from brokers.smartapi_instruments import _FNO_FUT_TYPES

    def _parse_expiry(row):
        try:
            return datetime.strptime(row["expiry"], "%d%b%Y")
        except (KeyError, ValueError, TypeError):
            return None

    data = _load_scrip_master()
    name_u = _canon_underlying(underlying)
    cands = [
        row
        for row in data
        if row.get("exch_seg") == exchange
        and row.get("name") == name_u
        and row.get("instrumenttype") in _FNO_FUT_TYPES
    ]
    cands = [(row, _parse_expiry(row)) for row in cands]
    cands = [(row, exp) for row, exp in cands if exp is not None]
    if not cands:
        return pd.DataFrame()
    cands.sort(key=lambda pair: pair[1])
    target = _to_smartapi_expiry(expiry_dash)
    matches = [row for row, _exp in cands if row["expiry"] == target]
    if not matches:
        return pd.DataFrame()
    fut = matches[0]

    quotes = market_data.get_batch_quotes(
        exchange, [(fut.get("symbol"), fut.get("token"))], mode="FULL"
    )
    q = quotes.get(fut.get("symbol")) if quotes else None
    if not q:
        return pd.DataFrame()

    spot_quote = market_data.get_spot_quote(underlying)
    spot = spot_quote["ltp"] if spot_quote else 0.0
    ltp = safe_float(q.get("ltp"))
    prev_close = safe_float(q.get("close"))
    change = safe_float(q.get("netChange"))
    pct = safe_float(q.get("percentChange"))
    if not pct and prev_close and ltp:
        pct = round(((ltp - prev_close) / prev_close) * 100.0, 2)

    return pd.DataFrame(
        [
            {
                "Contract": fut.get("symbol"),
                "Underlying": underlying,
                "Expiry": _from_smartapi_expiry(fut["expiry"]),
                "LTP": ltp,
                "Change": change,
                "PctChange": pct,
                "Open": safe_float(q.get("open")),
                "High": safe_float(q.get("high")),
                "Low": safe_float(q.get("low")),
                "PrevClose": prev_close,
                "Volume": safe_float(q.get("tradeVolume")),
                "Turnover": None,
                "OI": safe_float(q.get("opnInterest")),
                "Spot": spot,
                "Basis": round(ltp - spot, 2) if spot else None,
            }
        ]
    )


# ── VIX ──────────────────────────────────────────────────────────────────
# Gap #2 continued: not in smartapi_client.py's INDEX_TOKENS. Verified
# directly against the live scrip master (2026-07-14):
#   token=99926017, tradingsymbol="India VIX", exch_seg=NSE
_VIX_TRADINGSYMBOL = "India VIX"
_VIX_TOKEN = "99926017"
_NSE_TICKER_SYMBOLS = ["NIFTY", "BANKNIFTY", "MIDCPNIFTY", "FINNIFTY"]
_BSE_TICKER_SYMBOLS = ["SENSEX", "BANKEX", "SENSEX50"]
_TICKER_SYMBOLS = _NSE_TICKER_SYMBOLS + _BSE_TICKER_SYMBOLS

# Cache for the batched fetch each tick — populated once by
# fetch_all_pills_and_vix_batched(), then read by the three thin wrapper
# functions below so existing callers (ThreadPoolExecutor submissions in
# option_chain_json.py) don't need to change at all.
_BATCH_CACHE = TickScopedDict()


def _throttled_warning(key: str, msg: str, cooldown_s: float = 60.0) -> None:
    """Emit `msg` at most once per `cooldown_s` per `key`.

    A dead provider (stale Kite access token, Shoonya outage) currently
    fails the same way on EVERY tick — 8 spot-quote warnings per ~1.5s
    cycle drowns the log and hides the one useful diagnostic. First
    occurrence logs a WARNING, repeats within the window stay silent so
    the operator sees the cause once and then only hears about recovery.
    """
    now = time.monotonic()
    last = _WARN_COOLDOWNS.get(key)
    if last is not None and (now - last) < cooldown_s:
        return
    _WARN_COOLDOWNS[key] = now
    logger.warning(msg)


_WARN_COOLDOWNS: dict[str, float] = {}


def fetch_all_pills_and_vix_batched():
    """Replaces 6 separate ltpData calls (each throttled at 1.0s globally)
    with 2 batched getMarketData calls (0.35s each) — NIFTY/BANKNIFTY/
    MIDCPNIFTY/FINNIFTY/India VIX on NSE in one call, SENSEX on BSE in a
    second call. Was costing ~6s/tick in pure rate-limit wait; now ~0.7s.
    Populates _BATCH_CACHE; call this ONCE per tick before the three
    wrapper functions below.

    Uses get_batch_quotes_by_token() (keyed by token, re-mapped here to
    each symbol's own short code) rather than get_batch_quotes() (keyed
    by Angel's own tradingSymbol display string). get_batch_quotes()'s
    own docstring documents why: for NIFTY/BANKNIFTY/MIDCPNIFTY, Angel's
    returned tradingSymbol does not match the plain short code sent on
    the request, so re-keying results by the short code silently drops
    those rows — this function used to do exactly that, which is why
    _BATCH_CACHE (and everything reading it: fetch_ticker_payload_smartapi,
    fetch_vix_smartapi) never actually had a NIFTY entry even though the
    request itself succeeded and Angel returned NIFTY's row under a
    different tradingSymbol string."""
    index_tokens = market_data.index_tokens()

    if not index_tokens:
        # Providers without an index-token model (Kite/Breeze — see their
        # index_tokens() in brokers/market_data.py) get the same pills via
        # one get_spot_quote() per index instead of a token batch. Kept
        # inside this function so the _BATCH_CACHE consumers below
        # (fetch_ticker_payload_smartapi/fetch_vix_smartapi/fetch_sensex_
        # ticker_smartapi) stay provider-agnostic.
        from brokers.market_data import get_active_provider

        spot_quotes = {}
        for sym in _TICKER_SYMBOLS:
            try:
                q = market_data.get_spot_quote(sym)
            except Exception as exc:
                _throttled_warning(
                    f"spot:{sym}",
                    f"[{get_active_provider()}] spot quote {sym} failed: {exc}",
                )
                q = None
            if q and q.get("ltp"):
                spot_quotes[sym] = q
        try:
            vix_q = market_data.get_spot_quote("INDIA VIX")
        except Exception as exc:
            _throttled_warning(
                "spot:INDIA VIX",
                f"[{get_active_provider()}] VIX spot quote failed: {exc}",
            )
            vix_q = None
        nse_quotes = {
            sym: spot_quotes[sym] for sym in _NSE_TICKER_SYMBOLS if sym in spot_quotes
        }
        if vix_q and vix_q.get("ltp"):
            nse_quotes[_VIX_TRADINGSYMBOL] = vix_q
        bse_quotes = {
            sym: spot_quotes[sym] for sym in _BSE_TICKER_SYMBOLS if sym in spot_quotes
        }
        _BATCH_CACHE.refill(nse_quotes, bse_quotes)
        return

    nse_pairs = [
        (sym, index_tokens[sym]["token"])
        for sym in _NSE_TICKER_SYMBOLS
        if sym in index_tokens
    ]
    vix_token = index_tokens.get("INDIAVIX", {}).get("token", _VIX_TOKEN)
    nse_pairs.append((_VIX_TRADINGSYMBOL, vix_token))

    def _normalize_batch_quote(row: dict | None) -> dict | None:
        if not row:
            return None
        if "ltp" in row or "close" in row:
            return row
        if "last_price" in row:
            return {
                "ltp": safe_float(row.get("last_price")),
                "close": safe_float(row.get("close")),
                "open": safe_float(row.get("open")),
                "high": safe_float(row.get("high")),
                "low": safe_float(row.get("low")),
                "volume": safe_float(row.get("volume")),
                "oi": safe_float(row.get("oi")),
                "net_change": safe_float(row.get("net_change")),
                "pct_change": safe_float(row.get("pct_change")),
            }
        return row

    nse_by_token = market_data.get_batch_quotes_by_token("NSE", nse_pairs, mode="FULL")
    nse_quotes = {
        sym: _normalize_batch_quote(nse_by_token[str(token)])
        for sym, token in nse_pairs
        if str(token) in nse_by_token
    }

    bse_pairs = [
        (sym, index_tokens[sym]["token"])
        for sym in _BSE_TICKER_SYMBOLS
        if sym in index_tokens
    ]
    bse_quotes = {}

    for sym, _token in bse_pairs:
        try:
            row = market_data.get_spot_quote(sym)
            row = _normalize_batch_quote(row)

            if row:
                bse_quotes[sym] = row

        except Exception as exc:
            logger.warning(
                "BSE ticker spot quote %s failed: %s",
                sym,
                exc,
            )

    _BATCH_CACHE.refill(nse_quotes, bse_quotes)


def fetch_vix_smartapi() -> tuple[float | None, float]:
    """Return VIX from the active broker, with public NSE fallback.

    India VIX is not consistently exposed by every broker's instrument
    universe (notably some polling-only providers). A missing broker row must
    not turn into the engine's synthetic/default volatility when NSE's public
    all-indices endpoint is still available, so fall back only for a missing
    or unusable cached quote.
    """
    d = _BATCH_CACHE.get(_VIX_TRADINGSYMBOL)
    ltp = safe_float(d.get("ltp")) if d else None
    if ltp:
        close = safe_float(d.get("close"))
        chg_pct = round((ltp - close) / close * 100.0, 2) if close else 0.0
        return ltp, chg_pct

    try:
        from market_api import get_unified_market_data

        public_vix, public_change, _ = get_unified_market_data()
        public_vix = safe_float(public_vix)
        if public_vix:
            _throttled_warning(
                "vix:public-fallback",
                "VIX missing from broker quote; using public NSE VIX fallback",
            )
            return public_vix, safe_float(public_change) or 0.0
    except Exception as exc:
        _throttled_warning("vix:public-fallback", f"Public NSE VIX fallback failed: {exc}")

    _throttled_warning("vix:missing", "VIX unavailable from broker and public NSE fallback")
    return None, 0.0


def _index_quote_to_ticker_entry(symbol: str, quote: dict | None) -> dict | None:
    if not quote:
        return None
    ltp, close = quote.get("ltp"), quote.get("close")
    change = round(ltp - close, 2) if (ltp is not None and close) else 0.0
    pct = round((change / close) * 100.0, 2) if close else 0.0
    return {
        "Symbol": symbol,
        "BackendSymbol": symbol,
        "Last Price": ltp,
        "% Change": pct,
        "Change": change,
        "Prev Close": close,
    }


def fetch_ticker_payload_smartapi(symbols=None) -> list:
    """Now reads from _BATCH_CACHE instead of firing one ltpData call per
    symbol via ThreadPoolExecutor."""
    symbols = symbols or _TICKER_SYMBOLS
    payload = []
    for sym in symbols:
        d = _BATCH_CACHE.get(sym)
        entry = (
            _index_quote_to_ticker_entry(
                sym,
                {
                    "ltp": safe_float(d.get("ltp")),
                    "close": safe_float(d.get("close")),
                },
            )
            if d
            else None
        )
        if entry:
            payload.append(entry)
    return payload


def fetch_sensex_ticker_smartapi():
    d = _BATCH_CACHE.get("SENSEX")
    quote = (
        {
            "ltp": safe_float(d.get("ltp")),
            "close": safe_float(d.get("close")),
        }
        if d
        else None
    )
    return _index_quote_to_ticker_entry("SENSEX", quote)


# Provider-neutral public names.  The suffixed forms above are retained for
# compatibility with integrations that imported the former module directly.
fetch_vix = fetch_vix_smartapi
fetch_ticker_payload = fetch_ticker_payload_smartapi
fetch_sensex_ticker = fetch_sensex_ticker_smartapi


__all__ = [
    "_canon_underlying",
    "fetch_all_pills_and_vix_batched",
    "fetch_futures_wide",
    "fetch_option_chain_wide",
    "fetch_sensex_ticker",
    "fetch_sensex_ticker_smartapi",
    "fetch_ticker_payload",
    "fetch_ticker_payload_smartapi",
    "fetch_vix",
    "fetch_vix_smartapi",
    "get_available_expiries",
]