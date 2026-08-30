from __future__ import annotations
from brokers.smartapi.client import (
    _load_scrip_master,
    _get_strike_interval,
    _round_to_strike,
)
import logging
from datetime import datetime, date
import pandas as pd
from application.market_pipeline.utils import (
    _canon_underlying,
    safe_float,
)
from brokers.market_data import market_data

from market.expiry.instrument_expiries import (
    available_option_expiries,
    from_instrument_expiry as _from_smartapi_expiry,
    to_instrument_expiry as _to_smartapi_expiry,
)

from market.instruments.lot_sizes import get_lot_size as _lot_size

from market.option_chain.oi_change import (
    PreviousCloseOiTracker,
)

from oi.pricing import solve_iv

logger = logging.getLogger(__name__)

ANNUAL_RISK_FREE_RATE_DEFAULT = 0.07

def get_available_expiries(underlying: str, exchange: str = "NFO") -> list[str]:
    """Replacement for the expiryDates list NSE's option-chain-v3 gives for
    free — needed by NEAR/MONTHLY calendar-spread slot resolution."""
    return available_option_expiries(
        _load_scrip_master(),
        underlying,
        exchange=exchange,
        canonicalize=_canon_underlying,
    )


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
_SEED_RETRY_COOLDOWN_SEC = 30


def _load_public_oi_frame(underlying, expiry):
    from market.providers.nse_bse_client import (
        fetch_option_chain,
        parse_option_chain_response,
    )

    return parse_option_chain_response(fetch_option_chain(underlying, expiry), expiry)


_OI_CHANGE_TRACKER = PreviousCloseOiTracker(
    _load_public_oi_frame,
    retry_cooldown_seconds=_SEED_RETRY_COOLDOWN_SEC,
    logger=logger,
)
_day_open_oi = _OI_CHANGE_TRACKER.anchors
_seed_state = _OI_CHANGE_TRACKER.seed_state


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
    _OI_CHANGE_TRACKER.seed(underlying, expiry_dash)


def _chg_oi(
    underlying: str, expiry_dash: str, strike: float, side: str, current_oi
) -> float:
    return _OI_CHANGE_TRACKER.change(
        underlying,
        expiry_dash,
        strike,
        side,
        current_oi,
        seed=_seed_day_anchor_from_nse,
    )


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

    from brokers.market_data_registry import get_active_provider

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
                from market.providers.nse_bse import NseBseMarketData

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
                    raw_oi_before_lot_div = oi_now
                    oi_now = oi_now / lot_size
                    # DIAGNOSTIC (temporary): the /lot_size assumption above
                    # is documented as "pending live verification" for Kotak
                    # specifically (unlike Upstox/Kite/Shoonya/Breeze, whose
                    # share-quantity convention is confirmed against SDK
                    # docs). If Kotak's open_interest is actually already in
                    # lots, this division shrinks it a second time, producing
                    # an implausibly small in-lots OI (a live index ATM
                    # strike should be hundreds-to-thousands of lots, not
                    # single digits) and, downstream, a garbage ChgOI/PCR
                    # once diffed against the NSE-lot-anchored previous
                    # close. Log once per strike/side so real ticks can
                    # confirm or rule this out; remove once verified either
                    # way and the docstring caveat is resolved.
                    if provider == "KOTAK" and raw_oi_before_lot_div and oi_now < 5:
                        logger.warning(
                            "[kotak_oi_units] suspiciously small post-lot-div OI for "
                            "%s %s %s: raw=%.2f lot_size=%s -> %.4f lots — Kotak's "
                            "open_interest may already be in lots, not shares "
                            "(see option_chain.py's OI unit normalization note)",
                            underlying, strike_val, side,
                            raw_oi_before_lot_div, lot_size, oi_now,
                        )
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