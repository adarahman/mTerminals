import json
import re
import math
import os
import logging
from datetime import datetime, timezone
import pytz
import pandas as pd
import numpy as np
from storage.caches import ListSlot, MemoCache
from oi.pricing import DEFAULT_BASE_IV
from oi.capital_metrics import compute_chain_metrics

logger = logging.getLogger(__name__)

try:
    import orjson as _orjson
except ImportError:  # pragma: no cover
    _orjson = None


def _json_default(obj):
    """Coerce numpy/pandas leftovers so orjson/stdlib can serialize the payload.

    engine.py / decision_engine routinely leave np.float64/int64/bool_ and
    occasional pandas Timestamps in nested dicts; stdlib json used to accept
    some of these via float() fallbacks in custom paths, but orjson is
    strict and raises TypeError without a default handler.
    """
    if isinstance(obj, np.generic):  # float64, int64, bool_, ...
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, set):
        return list(obj)
    # pandas NA / NaT
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    raise TypeError(f"Type is not JSON serializable: {type(obj)}")


def _write_dashboard_json(out_path: str, payload: dict) -> None:
    """Write payload to disk. Prefer orjson (fast); fall back to stdlib json."""
    if _orjson is not None:
        try:
            with open(out_path, "wb") as fh:
                fh.write(
                    _orjson.dumps(
                        payload,
                        default=_json_default,
                        option=_orjson.OPT_NON_STR_KEYS
                        | getattr(_orjson, "OPT_SERIALIZE_NUMPY", 0),
                    )
                )
            return
        except TypeError as e:
            # Still unserializable — don't crash the live tick; fall through
            # to stdlib which is more forgiving / uses the same default.
            logger.warning(f"[_write_dashboard_json] orjson dump failed ({e}); falling back to stdlib json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, default=_json_default)


# ── Volume-change snapshot store ──────────────────────────────────────────────
# Used by _compute_vol_changes() (ceVolChg/peVolChg) only. The OI-velocity
# fallback that used to share this store (_compute_vel_rows) is scrapped —
# the primary vel_df path (engine.py -> oi_analysis.get_oi_velocity, off the
# parquet-backed history) works reliably now, so no fallback is needed.
#
# In-memory, not disk-backed: since ws_server_live.py is one long-lived
# process (not restarted per tick), there is no need for this to survive a
# process restart — an in-memory module-level list gives the same lookback
# window and pruning as the old oi_snapshots.json version, with zero disk I/O.
_MAX_SNAP_AGE_MIN = 35
_OI_SNAPSHOTS_MEM = ListSlot()

def fmt_k(val):
    """Compact number formatter for clean JSON outputs."""
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "--"
    abs_val = abs(val)
    if abs_val >= 1_000_000:
        return f"{val / 1_000_000:.2f}M"
    if abs_val >= 1_000:
        return f"{val / 1_000:.1f}K"
    return f"{val:.0f}"

def fmt_num(val, decimals=0):
    """Thousands-separated number string for clean JSON numbers."""
    try:
        return f"{float(val):,.{decimals}f}"
    except (TypeError, ValueError):
        return "--"
        
def _load_snapshots():
    return _OI_SNAPSHOTS_MEM.get()

def _save_snapshots(snaps):
    _OI_SNAPSHOTS_MEM.set(snaps)

def _safe_num(val, default=0.0):
    """Coerce a cell to float, treating NaN/None/missing as `default`.
    Unlike `val or default`, this correctly catches NaN — NaN is truthy
    in Python, so `NaN or 0` evaluates to NaN, not 0, and silently slips
    through the old pattern used here. Illiquid strikes (common on
    lower-volume stocks, less so on NIFTY) frequently arrive as NaN
    rather than 0, which is what triggered the int(NaN) crash.
    """
    if val is None:
        return default
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(f) else f

def _record_oi_snapshot(master):
    """Stores OI AND Volume snapshots for velocity calculations."""
    snaps = _load_snapshots()
    now   = datetime.now()
    cutoff = _MAX_SNAP_AGE_MIN * 60
    snaps = [s for s in snaps
             if (now - datetime.fromisoformat(s[0])).total_seconds() <= cutoff]
    snap = {}
    for r in master.to_dict("records"):
        sk = str(int(_safe_num(r.get("strike", 0))))
        snap[sk] = [
            int(_safe_num(r.get("ce_oi", 0))),
            int(_safe_num(r.get("pe_oi", 0))),
            int(_safe_num(r.get("ce_volume", 0))),   # <-- volume stored
            int(_safe_num(r.get("pe_volume", 0))),   # <-- volume stored
        ]
    snaps.append([now.isoformat(), snap])
    _save_snapshots(snaps)


def _find_snapshot_near(snaps, window_minutes):
    """
    Given the snapshot log (oldest→newest) and a target lookback window in
    minutes, returns (old_iso_ts, old_snap) for the snapshot whose age is
    closest to that window (and at least half of it), or None if no
    snapshot qualifies. Used by _compute_vol_changes only (the OI-velocity
    fallback that used to also share this, _compute_vel_rows, is scrapped).
    """
    if len(snaps) < 2:
        return None
    now_ts = datetime.fromisoformat(snaps[-1][0])
    target_age = window_minutes * 60
    best = None
    best_diff = float("inf")
    for iso_ts, snap in snaps[:-1]:
        age  = (now_ts - datetime.fromisoformat(iso_ts)).total_seconds()
        diff = abs(age - target_age)
        if diff < best_diff and age >= target_age * 0.5:
            best_diff = diff
            best = (iso_ts, snap)
    return best


def _compute_vol_changes(master, window_minutes):
    """
    Computes volume changes (ceVolChg, peVolChg) for each strike
    using the snapshot log. Returns dict {strike: (ceVolChg, peVolChg)}.
    """
    snaps = _load_snapshots()
    found = _find_snapshot_near(snaps, window_minutes)
    if found is None:
        return {}
    _, old_snap = found
    vol_changes = {}
    for r in master.to_dict("records"):
        sk = str(int(_safe_num(r.get("strike", 0))))
        ce_vol_now = int(_safe_num(r.get("ce_volume", 0)))
        pe_vol_now = int(_safe_num(r.get("pe_volume", 0)))
        prev = old_snap.get(sk, [0, 0, ce_vol_now, pe_vol_now])
        ce_vol_old = prev[2] if len(prev) > 2 else ce_vol_now
        pe_vol_old = prev[3] if len(prev) > 3 else pe_vol_now
        ce_vol_chg = ce_vol_now - ce_vol_old
        pe_vol_chg = pe_vol_now - pe_vol_old
        vol_changes[int(sk)] = (ce_vol_chg, pe_vol_chg)
    return vol_changes

def _r(v, decimals=4):
    if isinstance(v, (np.generic, pd.Series)):
        v = v.item()
    elif hasattr(v, 'iloc'):
        v = v.iloc[0]
    if isinstance(v, (float, np.float32, np.float64)):
        if math.isnan(v) or math.isinf(v):
            return 0.0
        return round(float(v), decimals)
    try:
        return float(v)
    except Exception:
        return 0.0

def _nullable_r(v, decimals=4):
    """Round an optional numeric value without collapsing missing into zero."""
    try:
        if isinstance(v, (np.generic, pd.Series)):
            v = v.item()
        elif hasattr(v, 'iloc'):
            v = v.iloc[0]
        if v is None or pd.isna(v):
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, decimals)
    except Exception:
        return None

def _to_int(v):
    try:
        if isinstance(v, (np.generic, pd.Series)):
            v = v.item()
        return int(float(v))
    except Exception:
        return 0

def _safe_str(v, default="—"):
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


_BID_ASK_QTY_COLS = [
    "CE_BidQty", "CE_AskQty", "PE_BidQty", "PE_AskQty",
    "CE_BuyQty", "CE_SellQty", "PE_BuyQty", "PE_SellQty",
]
_bid_ask_qty_warned = False

def _build_bid_ask_map(df_clean):
    """
    Builds {strike: {ceBid, ceAsk, peBid, peAsk, ceChg, peChg,
    ceBidQty, ceAskQty, peBidQty, peAskQty,
    ceTotalBidQty, ceTotalAskQty, peTotalBidQty, peTotalAskQty}}
    from a cleaned df_clean (StrikePrice/CE_*/PE_* columns). Shared by the
    primary chain and every extra_chains entry so this mapping logic lives
    once.

    Column names confirmed against market_api.py's parse_option_chain_response:
    CE_BidQty/CE_AskQty = top-of-book (NSE buyQuantity1/sellQuantity1),
    CE_BuyQty/CE_SellQty = total book depth (NSE totalBuyQuantity/totalSellQuantity).
    The startup check below is a safety net in case that upstream mapping
    ever changes column names again — kept intentionally loud rather than
    letting a rename silently degrade back to exported zeros.
    """
    global _bid_ask_qty_warned
    if not _bid_ask_qty_warned:
        missing = [c for c in _BID_ASK_QTY_COLS if c not in df_clean.columns]
        if missing:
            logger.warning(
                f"[_build_bid_ask_map] df_clean is missing expected "
                f"bid/ask quantity columns {missing} — Bid/Ask Depth box "
                f"will export 0 for these. Check market_api.py's "
                f"parse_option_chain_response for a column rename."
            )
        _bid_ask_qty_warned = True

    bid_ask_map = {}
    for row in df_clean.to_dict("records"):
        sk = _to_int(row.get("StrikePrice", 0))
        if sk > 0:
            bid_ask_map[sk] = {
                "ceBid": _r(row.get("CE_BidPrice", 0), 2),
                "ceAsk": _r(row.get("CE_AskPrice", 0), 2),
                "peBid": _r(row.get("PE_BidPrice", 0), 2),
                "peAsk": _r(row.get("PE_AskPrice", 0), 2),
                # Real day-over-day LTP change, straight from NSE's own
                # CE.change / PE.change (already parsed by market_api.py's
                # parse_option_chain_response).
                "ceChg": _r(row.get("CE_Change", 0), 2),
                "peChg": _r(row.get("PE_Change", 0), 2),
                # Top-of-book size (NSE's buyQuantity1/sellQuantity1).
                "ceBidQty": _to_int(row.get("CE_BidQty", 0)),
                "ceAskQty": _to_int(row.get("CE_AskQty", 0)),
                "peBidQty": _to_int(row.get("PE_BidQty", 0)),
                "peAskQty": _to_int(row.get("PE_AskQty", 0)),
                # Aggregate depth across the whole book (NSE's
                # totalBuyQuantity/totalSellQuantity) — distinct from the
                # top-of-book qty above; a strike can be thin at the best
                # price but deep further into the book, or vice versa.
                "ceTotalBidQty": _to_int(row.get("CE_BuyQty", 0)),
                "ceTotalAskQty": _to_int(row.get("CE_SellQty", 0)),
                "peTotalBidQty": _to_int(row.get("PE_BuyQty", 0)),
                "peTotalAskQty": _to_int(row.get("PE_SellQty", 0)),
            }
    return bid_ask_map


def _build_capital_map(capital_df):
    """
    Keys oi.capital_metrics.compute_capital_metrics()'s output by int
    strike so _build_chain_rows can look up each row's capital-weighted
    fields alongside its master-table fields. Returns {} if capital_df is
    None/empty (e.g. an extra_chains expiry whose ctx_dict predates
    engine.py wiring capital_metrics in, or a symbol where compute_capital_
    metrics raised and the caller swallowed it) — every lookup then falls
    back to _build_chain_rows' own 0-defaults rather than KeyError'ing.
    """
    if capital_df is None or getattr(capital_df, "empty", True):
        return {}
    return {_to_int(r["strike"]): r for r in capital_df.to_dict("records")}


def _build_chain_rows(master, atm_strike, bid_ask_map, capital_map=None):
    """
    Builds the list of chain-row dicts (one per strike) from a master
    dataframe, an ATM strike, and a bid/ask lookup (from
    _build_bid_ask_map). Shared by the primary chain and every
    extra_chains entry.

    capital_map (optional): {strike: row-dict} from _build_capital_map(),
    i.e. oi.capital_metrics.compute_capital_metrics()'s per-strike output.
    Adds the capital-weighted fields (premium locked, capital flow,
    premium turnover, delta/gamma exposure) alongside the existing OI/IV
    fields. Optional IV/capital analytics use _nullable_r() so unavailable
    data remains JSON null instead of being silently converted to zero.
    """
    capital_map = capital_map or {}
    rows = []
    for r in master.to_dict("records"):
        sk = _to_int(r["strike"])
        ba = bid_ask_map.get(sk, {})
        cm = capital_map.get(sk, {})
        rows.append({
            "strike":    sk,
            "atm":       sk == atm_strike,
            "atmStrike": atm_strike,          # int — HTML client-side filter anchor
            "ceLTP":    _r(r.get("ce_ltp",     0), 2),
            "ceBid":    ba.get("ceBid", 0.0),
            "ceAsk":    ba.get("ceAsk", 0.0),
            "ceChg":    ba.get("ceChg", 0.0),
            "ceBidQty": ba.get("ceBidQty", 0),
            "ceAskQty": ba.get("ceAskQty", 0),
            "ceTotalBidQty": ba.get("ceTotalBidQty", 0),
            "ceTotalAskQty": ba.get("ceTotalAskQty", 0),
            "ceOI":     _to_int(r.get("ce_oi",     0)),
            "ceChgOI":  _to_int(r.get("ce_oi_chg", 0)),
            "ceVol":    _to_int(r.get("ce_volume", 0)),
            "ceIV":     _nullable_r(r.get("ce_iv"), 2),
            "ceSignal": str(r.get("ce_signal", "")),
            "peLTP":    _r(r.get("pe_ltp",     0), 2),
            "peBid":    ba.get("peBid", 0.0),
            "peAsk":    ba.get("peAsk", 0.0),
            "peChg":    ba.get("peChg", 0.0),
            "peBidQty": ba.get("peBidQty", 0),
            "peAskQty": ba.get("peAskQty", 0),
            "peTotalBidQty": ba.get("peTotalBidQty", 0),
            "peTotalAskQty": ba.get("peTotalAskQty", 0),
            "peOI":     _to_int(r.get("pe_oi",     0)),
            "peChgOI":  _to_int(r.get("pe_oi_chg", 0)),
            "peVol":    _to_int(r.get("pe_volume", 0)),
            "peIV":     _nullable_r(r.get("pe_iv"), 2),
            "peSignal": str(r.get("pe_signal", "")),
            # ── capital-weighted fields (oi.capital_metrics) ────────────
            # Rupee premium locked = OI x LTP, day-session capital flow =
            # ChgOI x LTP (NOT intraday — see capital_metrics.py module
            # docstring), and delta/gamma exposure = OI x Greek x spot(^2).
            "cePremiumLocked":  _nullable_r(cm.get("ce_premium_locked"), 2),
            "pePremiumLocked":  _nullable_r(cm.get("pe_premium_locked"), 2),
            "ceNotionalExposure": _nullable_r(cm.get("ce_notional_exposure"), 2),
            "peNotionalExposure": _nullable_r(cm.get("pe_notional_exposure"), 2),
            "ceCapitalFlow":    _nullable_r(cm.get("ce_capital_flow"), 2),
            "peCapitalFlow":    _nullable_r(cm.get("pe_capital_flow"), 2),
            "cePremiumTurnover": _nullable_r(cm.get("ce_premium_turnover"), 2),
            "pePremiumTurnover": _nullable_r(cm.get("pe_premium_turnover"), 2),
            "ceDeltaExposure":  _nullable_r(cm.get("ce_delta_exposure"), 2),
            "peDeltaExposure":  _nullable_r(cm.get("pe_delta_exposure"), 2),
            "ceGammaExposure":  _nullable_r(cm.get("ce_gamma_exposure"), 2),
            "peGammaExposure":  _nullable_r(cm.get("pe_gamma_exposure"), 2),
            # oi.footprint_score.compute_footprint_score() — percentile-
            # ranked composite, "how loud is this strike right now vs the
            # rest of the currently-visible chain" (see that module's
            # docstring for why percentile rank rather than a fixed scale).
            "footprintScore":   _nullable_r(cm.get("footprint_score"), 1),
            # Canonical component percentile ranks used to explain WHY the
            # composite score is high. These are produced by footprint_score.py;
            # the UI only ranks/presents them and never recomputes the score.
            "footprintFactors": {
                "capitalActivity": _nullable_r(cm.get("footprint_pct_capital_activity"), 1),
                "oiChangeActivity": _nullable_r(cm.get("footprint_pct_oi_change_activity"), 1),
                "turnoverActivity": _nullable_r(cm.get("footprint_pct_turnover_activity"), 1),
                "gammaActivity": _nullable_r(cm.get("footprint_pct_gamma_activity"), 1),
                "deltaActivity": _nullable_r(cm.get("footprint_pct_delta_activity"), 1),
                "writingActivity": _nullable_r(cm.get("footprint_pct_writing_activity"), 1),
            },
        })
    return rows


# ── Panels ────────────────────────────────────────────────────────────────────
def _build_signals(ctx_dict, engine_result=None):
    def bias_cls(b):
        if not b: return "neutral"
        b = b.lower()
        if "bull" in b or "long" in b:  return "bull"
        if "bear" in b or "short" in b: return "bear"
        return "neutral"

    fut        = _safe_str(ctx_dict.get("fut_signal"))
    vix_regime = _safe_str(ctx_dict.get("vix_regime"))
    trap       = _safe_str(ctx_dict.get("trap_warn"))
    pcr_sent   = _safe_str(ctx_dict.get("pcr_sentiment"))
    spot_chg   = _r(ctx_dict.get("spot_chg_pct", 0.0), 2)

    signals = [
        {"label": "Futures signal", "value": fut,       "cls": bias_cls(fut)},
        {"label": "PCR sentiment",  "value": pcr_sent,  "cls": bias_cls(pcr_sent)},
        {"label": "VIX regime",     "value": vix_regime,
         "cls": "bull" if "low" in vix_regime.lower() else ("bear" if "high" in vix_regime.lower() else "neutral")},
        {"label": "Spot change",    "value": f"{'+' if spot_chg >= 0 else ''}{spot_chg:.2f}%",
         "cls": "bull" if spot_chg > 0 else ("bear" if spot_chg < 0 else "neutral")},
        {"label": "Trap warning",   "value": trap,
         "cls": "bear" if trap.lower() not in ("none", "—", "") else "neutral"},
    ]

    if engine_result is not None:
        for attr in ("extra_signals", "signals"):
            extras = getattr(engine_result, attr, None)
            if isinstance(extras, list):
                for sig in extras:
                    if isinstance(sig, dict) and "label" in sig and "value" in sig:
                        signals.append({
                            "label": _safe_str(sig.get("label")),
                            "value": _safe_str(sig.get("value")),
                            "cls":   _safe_str(sig.get("cls", "neutral")),
                        })
                break
    return signals


def _build_strategies(ctx_dict, engine_result=None, chain_rows=None):
    """
    Builds strategy cards. Legs always carry: type, strike, action, lots, ltp.
    chain_rows: list of chain dicts — used to resolve live LTPs for each leg.
    """
    # LTP lookup: {strike: {"CE": ltp, "PE": ltp}}
    ltp_map = {}
    if chain_rows:
        for row in chain_rows:
            sk = row["strike"]
            ltp_map[sk] = {"CE": row.get("ceLTP", 0.0), "PE": row.get("peLTP", 0.0)}

    def _resolve_ltp(leg):
        ltp = leg.get("ltp", leg.get("LTP", 0.0)) or 0.0
        if ltp == 0.0 and ltp_map:
            sk  = leg.get("strike", 0)
            typ = str(leg.get("type", "")).upper()
            ltp = ltp_map.get(sk, {}).get(typ, 0.0)
        return round(float(ltp), 2)

    def _make_leg(type_, strike, action, lots=1, expiry=None):
        return {
            "type":   type_,
            "strike": strike,
            "action": action,
            "lots":   lots,
            "ltp":    ltp_map.get(strike, {}).get(type_, 0.0),
            "expiry": expiry or ctx_dict.get("expiry_label", ""),
        }

    def _ltp(type_, strike):
        """Resolve live LTP for a given type+strike from chain."""
        return float(ltp_map.get(strike, {}).get(type_, 0.0))

    def _net_credit(legs):
        """Sum of all SELL premiums minus BUY premiums. Positive = net credit."""
        total = 0.0
        for leg in legs:
            ltp = float(leg.get("ltp", 0.0))
            if leg["action"] == "SELL":
                total += ltp
            else:
                total -= ltp
        return round(total, 2)

    def _grade(iv_rank, bias, strategy_name):
        """Score strategy suitability: A/B/C based on IV regime and bias alignment."""
        sn = strategy_name.lower()
        high_iv = iv_rank >= 60
        low_iv  = iv_rank <= 35
        # Short vol strategies — want high IV
        if any(x in sn for x in ("straddle", "strangle", "condor", "butterfly")):
            if high_iv: return "A"
            if low_iv:  return "C"
            return "B"
        # Directional debit spreads — want low-moderate IV
        if "bull call" in sn or "bear put" in sn:
            if low_iv:  return "A"
            if high_iv: return "C"
            return "B"
        # Credit spreads — ok in most IV environments
        if "bull put" in sn or "bear call" in sn:
            if high_iv: return "A"
            return "B"
        return "B"

    # ── Generic strategy shapes ─────────────────────────────────────────
    # The four fallback branches below (high-IV / bull / bear / neutral)
    # all assemble one of these same four trade shapes, just with different
    # strikes/rationale text. Factored out so the leg/credit/breakeven math
    # for each shape lives in exactly one place.

    def _short_straddle(atm, ce_prem, pe_prem, iv_rank, bias, lot_size_ctx, rationale):
        legs = [_make_leg("CE", atm, "SELL"), _make_leg("PE", atm, "SELL")]
        credit = _net_credit(legs)
        credit_actual = round(credit if credit > 0 else (ce_prem + pe_prem), 2)
        be_lo = round(atm - credit_actual, 0)
        be_hi = round(atm + credit_actual, 0)
        return {
            "name":      "Short Straddle",
            "rationale": rationale(credit_actual, be_lo, be_hi),
            "legs": legs,
            "maxProfit":  f"₹{credit_actual:.0f} ({credit_actual * lot_size_ctx:,.0f} total)",
            "maxLoss":    f"Unlimited beyond {be_lo:.0f} / {be_hi:.0f}",
            "breakevens": [be_lo, be_hi],
            "grade":      _grade(iv_rank, bias, "Short Straddle"),
        }

    def _iron_condor(atm, step, iv_rank, bias, lot_size_ctx, rationale):
        legs = [
            _make_leg("PE", atm - 2*step, "BUY"),
            _make_leg("PE", atm - step,   "SELL"),
            _make_leg("CE", atm + step,   "SELL"),
            _make_leg("CE", atm + 2*step, "BUY"),
        ]
        credit = _net_credit(legs)
        max_loss = round(step - credit, 2)
        be_lo = round((atm - step) - credit, 0)
        be_hi = round((atm + step) + credit, 0)
        return {
            "name":      "Iron Condor",
            "rationale": rationale(credit, max_loss, be_lo, be_hi),
            "legs": legs,
            "maxProfit":  f"₹{credit:.0f} ({credit * lot_size_ctx:,.0f} total)",
            "maxLoss":    f"₹{max_loss:.0f} ({max_loss * lot_size_ctx:,.0f} total)",
            "breakevens": [be_lo, be_hi],
            "grade":      _grade(iv_rank, bias, "Iron Condor"),
        }

    def _debit_vertical(name, buy_type, buy_strike, sell_type, sell_strike, be_sign,
                         iv_rank, bias, lot_size_ctx, rationale):
        """Buy near strike, sell far strike — net debit. be_sign: +1 for
        breakeven = buy_strike + debit (calls), -1 for buy_strike - debit (puts)."""
        legs = [_make_leg(buy_type, buy_strike, "BUY"), _make_leg(sell_type, sell_strike, "SELL")]
        debit = -_net_credit(legs)
        width = abs(sell_strike - buy_strike)
        max_profit = round(width - debit, 2)
        be = round(buy_strike + be_sign * debit, 0)
        return {
            "name":      name,
            "rationale": rationale(debit, max_profit, be),
            "legs": legs,
            "maxProfit":  f"₹{max_profit:.0f} ({max_profit * lot_size_ctx:,.0f} total)",
            "maxLoss":    f"₹{debit:.0f} debit ({debit * lot_size_ctx:,.0f} total)",
            "breakevens": [be],
            "grade":      _grade(iv_rank, bias, name),
        }

    def _credit_vertical(name, sell_type, sell_strike, buy_type, buy_strike, be_sign,
                          iv_rank, bias, lot_size_ctx, rationale):
        """Sell near strike, buy far strike as protection — net credit.
        be_sign: +1 for breakeven = sell_strike + credit, -1 for sell_strike - credit."""
        legs = [_make_leg(sell_type, sell_strike, "SELL"), _make_leg(buy_type, buy_strike, "BUY")]
        credit = _net_credit(legs)
        width = abs(buy_strike - sell_strike)
        max_loss = round(width - credit, 2)
        be = round(sell_strike + be_sign * credit, 0)
        return {
            "name":      name,
            "rationale": rationale(credit, max_loss, be),
            "legs": legs,
            "maxProfit":  f"₹{credit:.0f} ({credit * lot_size_ctx:,.0f} total)",
            "maxLoss":    f"₹{max_loss:.0f} ({max_loss * lot_size_ctx:,.0f} total)",
            "breakevens": [be],
            "grade":      _grade(iv_rank, bias, name),
        }

    strategies = []

    # ── Try engine_result first ───────────────────────────────────────
    def _parse_leg_string(leg_str: str, expiry_label: str) -> dict | None:
        """Parse engine.py string legs like 'Buy 24500 CE @ ₹120.5' into dicts."""
        if not isinstance(leg_str, str) or not leg_str.strip():
            return None
        s2 = leg_str.replace("₹", "").replace(",", "").strip()
        lots_m = re.search(r'(\d+)x', s2)
        lots = int(lots_m.group(1)) if lots_m else 1
        action = "BUY" if s2.upper().startswith("BUY") else "SELL"
        typ_m  = re.search(r'(?<![A-Z])(CE|PE)(?![A-Z])', s2, re.IGNORECASE)
        typ    = typ_m.group(1).upper() if typ_m else ""
        strike_m = re.search(r'(\d{4,6})', s2)
        strike = int(strike_m.group(1)) if strike_m else 0
        ltp_m  = re.search(r'@\s*([\d.]+)', s2)
        raw_ltp = float(ltp_m.group(1)) if ltp_m else 0.0
        live = ltp_map.get(strike, {}).get(typ, 0.0) if typ and strike else 0.0
        ltp = live if live > 0 else raw_ltp
        if not typ or not strike:
            return None
        return {"type": typ, "strike": strike, "action": action,
                "lots": lots, "ltp": round(ltp, 2), "expiry": expiry_label}

    def _parse_breakeven_str(be_str: str) -> list:
        parts = re.split(r'[/|]', be_str)
        result = []
        for p in parts:
            m = re.search(r'([\d,]+(?:\.\d+)?)', p.replace(",", ""))
            if m:
                try:
                    result.append(float(m.group(1)))
                except ValueError:
                    pass
        return result

    if engine_result is not None:
        for attr in ("strategies", "strategy_list", "recommended_strategies"):
            raw = getattr(engine_result, attr, None)
            if isinstance(raw, list) and raw:
                expiry_lbl = ctx_dict.get("expiry_label", str(ctx_dict.get("expiry", "")))
                iv_rank_e  = _r(ctx_dict.get("iv_rank", 35.0), 1)
                bias_e     = _safe_str(ctx_dict.get("bias", "Neutral")).lower()
                for s in raw:
                    if not isinstance(s, dict):
                        continue
                    legs = []
                    raw_legs = s.get("legs", [])
                    for leg in raw_legs:
                        if isinstance(leg, dict):
                            legs.append({
                                "type":   _safe_str(leg.get("type", leg.get("option_type", ""))),
                                "strike": int(float(leg.get("strike", 0))),
                                "action": _safe_str(leg.get("action", leg.get("side", "BUY"))),
                                "lots":   int(leg.get("lots", 1)),
                                "ltp":    _resolve_ltp(leg),
                                "expiry": _safe_str(leg.get("expiry", expiry_lbl)),
                            })
                        elif isinstance(leg, str) and leg.strip():
                            parsed = _parse_leg_string(leg, expiry_lbl)
                            if parsed:
                                legs.append(parsed)
                    _eng_net = sum(
                        l["ltp"] if l["action"] == "SELL" else -l["ltp"]
                        for l in legs
                    )
                    be_raw = s.get("breakevens", s.get("be", s.get("breakeven", s.get("break_even", []))))
                    if isinstance(be_raw, str):
                        be_raw = _parse_breakeven_str(be_raw)
                    elif not isinstance(be_raw, list):
                        be_raw = []
                    grade = _safe_str(s.get("grade", s.get("trade_grade", "")))
                    if not grade:
                        grade = _grade(iv_rank_e, bias_e, _safe_str(s.get("name", "")))
                    strategies.append({
                        "name":       _safe_str(s.get("name")),
                        "rationale":  _safe_str(s.get("rationale", s.get("desc", s.get("reason", "")))),
                        "legs":       legs,
                        "netCredit":  round(_eng_net, 2),
                        "maxProfit":  _safe_str(s.get("maxProfit", s.get("max_profit", ""))),
                        "maxLoss":    _safe_str(s.get("maxLoss",   s.get("max_loss",   ""))),
                        "breakevens": be_raw,
                        "grade":      grade,
                    })
                break

    # ── Fallback: build from ctx_dict with live LTPs ──────────────────
    if not strategies:
        bias     = _safe_str(ctx_dict.get("bias", "Neutral")).lower()
        iv_rank  = _r(ctx_dict.get("iv_rank", 35.0), 1)
        atm      = _to_int(ctx_dict.get("atm", 0))
        ce_prem  = _r(ctx_dict.get("ce_premium", 0.0))
        pe_prem  = _r(ctx_dict.get("pe_premium", 0.0))
        straddle = round(ce_prem + pe_prem, 0)
        step     = 100
        lot_size_ctx = _to_int(ctx_dict.get("lot_size", 75))

        if iv_rank > 60:
            strategies.append(_short_straddle(
                atm, ce_prem, pe_prem, iv_rank, bias, lot_size_ctx,
                rationale=lambda credit, lo, hi: (
                    f"IV rank {iv_rank:.0f} — elevated premium; range-bound conditions favour short vol. "
                    f"ATM CE ₹{_ltp('CE',atm):.1f} + PE ₹{_ltp('PE',atm):.1f} = "
                    f"₹{credit:.0f} credit per lot."
                ),
            ))
            strategies.append(_iron_condor(
                atm, step, iv_rank, bias, lot_size_ctx,
                rationale=lambda credit, max_loss, lo, hi: (
                    f"High IV rank {iv_rank:.0f} — sell {atm-step}/{atm+step} strangle, "
                    f"hedge with {atm-2*step}/{atm+2*step} wings. "
                    f"Net credit ₹{credit:.0f}."
                ),
            ))

        elif "bull" in bias:
            strategies.append(_debit_vertical(
                "Bull Call Spread", "CE", atm, "CE", atm + step, +1,
                iv_rank, bias, lot_size_ctx,
                rationale=lambda debit, max_profit, be: (
                    f"Bullish bias — buy {atm} CE ₹{_ltp('CE',atm):.1f}, "
                    f"sell {atm+step} CE ₹{_ltp('CE',atm+step):.1f}. "
                    f"Net debit ₹{debit:.0f}, max profit ₹{max_profit:.0f}."
                ),
            ))
            strategies.append(_credit_vertical(
                "Bull Put Spread", "PE", atm, "PE", atm - step, -1,
                iv_rank, bias, lot_size_ctx,
                rationale=lambda credit, max_loss, be: (
                    f"Bullish bias — sell {atm} PE ₹{_ltp('PE',atm):.1f}, "
                    f"buy {atm-step} PE ₹{_ltp('PE',atm-step):.1f}. "
                    f"Net credit ₹{credit:.0f}, keep if spot stays above {be:.0f}."
                ),
            ))

        elif "bear" in bias:
            strategies.append(_credit_vertical(
                "Bear Call Spread", "CE", atm, "CE", atm + step, +1,
                iv_rank, bias, lot_size_ctx,
                rationale=lambda credit, max_loss, be: (
                    f"Bearish bias — sell {atm} CE ₹{_ltp('CE',atm):.1f}, "
                    f"buy {atm+step} CE ₹{_ltp('CE',atm+step):.1f}. "
                    f"Net credit ₹{credit:.0f}, keep if spot stays below {be:.0f}."
                ),
            ))
            strategies.append(_debit_vertical(
                "Bear Put Spread", "PE", atm, "PE", atm - step, -1,
                iv_rank, bias, lot_size_ctx,
                rationale=lambda debit, max_profit, be: (
                    f"Bearish bias — buy {atm} PE ₹{_ltp('PE',atm):.1f}, "
                    f"sell {atm-step} PE ₹{_ltp('PE',atm-step):.1f}. "
                    f"Net debit ₹{debit:.0f}, max profit ₹{max_profit:.0f}."
                ),
            ))

        else:
            strategies.append(_iron_condor(
                atm, step, iv_rank, bias, lot_size_ctx,
                rationale=lambda credit, max_loss, lo, hi: (
                    f"Neutral / balanced OI — sell {atm-step}/{atm+step} strangle, "
                    f"hedge with {atm-2*step}/{atm+2*step} wings. "
                    f"Net credit ₹{credit:.0f}. Profitable if spot stays between "
                    f"{lo:.0f}–{hi:.0f}."
                ),
            ))
            strategies.append(_short_straddle(
                atm, ce_prem, pe_prem, iv_rank, bias, lot_size_ctx,
                rationale=lambda credit, lo, hi: (
                    f"Range-bound market — sell ATM CE ₹{_ltp('CE',atm):.1f} + "
                    f"PE ₹{_ltp('PE',atm):.1f} = ₹{credit:.0f} credit. "
                    f"Profitable between {lo:.0f}–{hi:.0f}."
                ),
            ))

    return strategies


def _build_risk(ctx_dict, engine_result=None):
    iv_rank   = _r(ctx_dict.get("iv_rank", 35.0), 1)
    atm_iv    = _r(ctx_dict.get("base_iv", DEFAULT_BASE_IV) * 100, 2)
    hv30      = _r(ctx_dict.get("hv30", 15.0), 1)
    iv_hv     = atm_iv - hv30
    iv_regime = "Rich" if iv_hv > 3 else ("Cheap" if iv_hv < -2 else "Fair")
    trade_grade = "—"
    if engine_result is not None:
        trade_grade = _safe_str(getattr(engine_result, "trade_grade", None) or
                                ctx_dict.get("trade_grade", "—"))
    return {
        "tradeGrade": trade_grade,
        "trapWarn":   _safe_str(ctx_dict.get("trap_warn", "None")),
        "ivRegime":   iv_regime,
        "ivHvSpread": _r(iv_hv, 2),
        "keyLevels": [
            {"label": "CE wall",  "value": _to_int(ctx_dict.get("ce_wall",  0)), "cls": "bear"},
            {"label": "PE wall",  "value": _to_int(ctx_dict.get("pe_wall",  0)), "cls": "bull"},
            {"label": "Max pain", "value": _to_int(ctx_dict.get("max_pain", 0)), "cls": "neutral"},
            {"label": "ATM",      "value": _to_int(ctx_dict.get("atm",      0)), "cls": "neutral"},
        ],
    }

from expiry_manager import make_expiry_manager
from decision.decision_engine import DecisionEngine

# ── VirtualOI coordinator ────────────────────────────────────────────────
# Was previously disabled: dispatch_tick() was fed tick-level fields
# (delta_volume/vwap_slope/imbalance/volatility/time_decay_factor) while
# the deployed models were trained on the real 7-column snapshot-delta
# schema (ce_vol_delta, pe_vol_delta, ce_oi_delta_lag, pe_oi_delta_lag,
# ce_iv_delta, pe_iv_delta, minutes_since_last) — predict() was silently
# failing every call, and the coordinator only ever held one model, so
# the PE side was never reachable at all. Both bugs are now fixed in
# ml/inference.py: on_tick()/_infer() consume the real schema,
# and VirtualOICoordinator routes each side to its own trained pipeline.
try:
    from ml.inference import load_virtual_oi_coordinator
    _VOI_COORDINATOR = load_virtual_oi_coordinator("model_registry")
except ImportError:
    _VOI_COORDINATOR = None

# ── FII/DII sentiment (dashboard display only — NOT fed into VirtualOI) ──
# The deployed HuberRegressor pipelines were trained on a fixed 6-column
# FEATURES schema (ml/features.py). Adding this to real_features
# below would be silently ignored by _infer() (it whitelists by column
# name), not an error but not useful either — so this stays a separate,
# dashboard-only field until the models are retrained with it included.
# Cached per calendar day: the EOD file only changes once daily, so
# re-reading the parquet every 5s poll tick would be wasted I/O.
try:
    from analytics.fii_dii_sentiment import get_feature_for_trading_day
except ImportError:
    get_feature_for_trading_day = None

# Combined cash+F&O bias report (analytics/fii_dii_market_bias.py) — same
# EOD cadence as fiiDiiSentiment above (cash flow + participant OI both
# update once/day), so it's cached the same way. Previously this was only
# computed inside ws_server_live.py's bridge_loop() for the /dashboard-relay
# feed the FII/DII modal opens on demand — meaning the bias summary was
# invisible until a person opened that modal. Caching + exposing it here
# puts it on the main dashboard's regular tick instead, so ExecView.
# buildFiiDiiSummaryCard() can render it without a live relay connection.
try:
    from analytics.fii_dii_market_bias import get_market_bias_report
except ImportError:
    get_market_bias_report = None

from analytics.smart_money_summary import compute_smart_money_summary
from analytics.capital_futures_confirmation import (
    classify_capital_vs_futures, compute_capital_confirmation,
    detect_futures_options_divergence,
)
from oi.footprint_score import (
    compute_footprint_score, rank_footprint_strikes, compute_capital_concentration,
)

if os.environ.get("MTERMINALS_NO_SMARTAPI") == "1":
    market_data = None
else:
    try:
        from brokers.market_data import market_data
    except ImportError:
        market_data = None

# Minimal hardcoded fallback (mirrors the old COMMON_SYMBOLS in
# dashboard.js) — only used if the ScripMaster-backed lookup below fails
# (e.g. no cached/downloadable ScripMaster available), so the top-bar
# symbol picker always has at least the indices instead of going empty.
_FNO_SYMBOLS_FALLBACK = {
    "indices": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"],
    "stocks": [],
}

_FII_DII_CACHE = MemoCache()  # keyed by trading-day date


def _get_cached_fii_dii_sentiment():
    if get_feature_for_trading_day is None:
        return None
    today = datetime.now().date()
    if today not in _FII_DII_CACHE:
        try:
            features = get_feature_for_trading_day(datetime.now())
        except Exception as e:
            logger.warning(f"[_get_cached_fii_dii_sentiment] FII/DII sentiment lookup failed: {e}")
            features = None
        _FII_DII_CACHE.clear()  # only ever one day's worth needed at a time
        _FII_DII_CACHE.set(today, features)
    return _FII_DII_CACHE.get(today)


_FII_DII_BIAS_CACHE = MemoCache()  # keyed by trading-day date


def _get_cached_fii_dii_bias():
    if get_market_bias_report is None:
        return None
    today = datetime.now().date()
    if today not in _FII_DII_BIAS_CACHE:
        try:
            report = get_market_bias_report(datetime.now())
        except Exception as e:
            logger.warning(f"[_get_cached_fii_dii_bias] FII/DII bias report FAILED: {e}")
            report = None
        _FII_DII_BIAS_CACHE.clear()  # only ever one day's worth needed at a time
        _FII_DII_BIAS_CACHE.set(today, report)
    return _FII_DII_BIAS_CACHE.get(today)


def _get_fno_symbols():
    """{"indices": [...], "stocks": [...]} for the top-bar symbol picker —
    every underlying currently carrying live F&O contracts, sourced from
    the ScripMaster via MarketData.get_fno_underlyings() (see
    brokers/market_data.py for caching/refresh details). Falls back to
    just the known indices if that lookup isn't available for any
    reason, so the dropdown never ends up empty."""
    if market_data is None:
        try:
            # --no-smartapi disables authenticated broker services, not the
            # public daily ScripMaster. Keep the complete dropdown available
            # without constructing or logging in a SmartAPI session.
            from brokers.smartapi_instruments import get_fno_underlyings
            symbols = get_fno_underlyings()
            if symbols.get("indices") or symbols.get("stocks"):
                return symbols
        except Exception as e:
            logger.warning(
                f"[_get_fno_symbols] Public ScripMaster universe lookup failed: {e}"
            )
        return _FNO_SYMBOLS_FALLBACK
    try:
        return market_data.get_fno_underlyings()
    except Exception as e:
        logger.warning(f"[_get_fno_symbols] F&O underlying list lookup failed: {e}")
        return _FNO_SYMBOLS_FALLBACK

# ── Main export function ───────────────────────────────────────────────────────
def export_dashboard_json(
    df_clean,
    master,
    ctx_dict,
    SYMBOL,
    EXPIRY,
    dte,
    engine_result=None,
    out_path="mTerminals.json",
    expiry_dates=None,        # list of available expiry date strings
    extra_chains=None,        # dict: { "07-Jul-2026": (df_clean, master, ctx_dict, dte), ... }
    use_virtual_oi=True,      # False (from --no-virtual-oi) skips the per-strike/per-side
                               # dispatch_tick() inference loop below entirely
    contributors=None,        # list of {symbol, weightage, ltp, change, pct_change,
                               # point_impact} for SYMBOL's own index basket — see
                               # _compute_index_contributors() in option_chain_json.py.
                               # None/empty for symbols with no matching NSE basket.
    all_indices=None,         # list of {Symbol, Last Price, % Change} for ticker strip
    price_source="EQ",        # "EQ" or "FUT" — option_chain_json.PRICE_SOURCE at call
                               # time. Passed as a param (not imported directly) to
                               # avoid a circular import — option_chain_json.py is this
                               # function's only caller.
    futures_expiry=None,      # near-month futures expiry string when price_source="FUT"
                               # (option_chain_json.FUTURES_EXPIRY at call time), else None.
):
    logger.info(f"[export_dashboard_json] Assembling frontend payload for {SYMBOL}...")

    # ── 1. lastUpdated (ISO timestamp with timezone) ──────────────────────
    india_tz = pytz.timezone('Asia/Kolkata')
    last_updated = datetime.now(india_tz).isoformat(timespec='seconds')
    refresh_time = datetime.now().strftime("%H:%M:%S")   # kept for compatibility

    spot = _r(ctx_dict.get("spot", 0), 2)

    # ── 2. ATM strike ─────────────────────────────────────────────────
    # ctx_dict["atm"] is already computed by engine.py's build_engine_result
    # on every normal tick, so this fallback lookup almost never actually
    # needs to run — but it used to run an unconditional full iterrows()
    # pass over the whole master DataFrame every single tick regardless,
    # just to build a list whose result was thrown away whenever ctx_dict
    # already had "atm" (i.e. nearly always). Now it only runs — and only
    # as a vectorized column op, not a Python loop — when actually needed.
    atm_strike = _to_int(ctx_dict.get("atm", 0))
    if atm_strike == 0 and spot > 0:
        valid_strikes = master.loc[master["strike"] > 0, "strike"]
        if not valid_strikes.empty:
            atm_strike = _to_int(valid_strikes.iloc[(valid_strikes - spot).abs().argmin()])

    # ── 3. Build bid/ask lookup from df_clean ──────────────────────────
    bid_ask_map = _build_bid_ask_map(df_clean)

    # ── 4. Chain rows ─────────────────────────────────────────────────
    # footprint_score wrapping added here (not inside _build_capital_map
    # itself) so _build_capital_map stays a pure "key this df by strike"
    # helper — compute_footprint_score() is the thing that actually adds
    # the column, same separation compute_capital_metrics()/
    # compute_chain_metrics() already keep.
    capital_map = _build_capital_map(compute_footprint_score(ctx_dict.get("capital_metrics")))
    chain_rows = _build_chain_rows(master, atm_strike, bid_ask_map, capital_map)

    # ── 5. Compute Volume Changes ──────────────────────────────────────
    vol_changes = _compute_vol_changes(master, 5)   # 5-min window
    for row in chain_rows:
        sk = row["strike"]
        ce_vol_chg, pe_vol_chg = vol_changes.get(sk, (0, 0))
        row["ceVolChg"] = ce_vol_chg   # <-- NEW
        row["peVolChg"] = pe_vol_chg   # <-- NEW

    # ── 5b. Virtual OI estimation (fills NSE 3-min cooling gap) ────────
    # Each strike/side gets its own estimator, keyed "SYMBOL_STRIKE" + side.
    # Confirmed OI is reconciled every time NSE actually refreshes (i.e.
    # every export cycle where ceOI/peOI genuinely changed from last run).
    #
    # Features come from engine_result.oi_history_snapshot — the same raw
    # per-strike poll-to-poll delta table (CE_OI_Delta, PE_OI_Delta,
    # CE_Volume_Delta, PE_Volume_Delta, CE_IV_Delta, PE_IV_Delta) that
    # build_training_warehouse.py trained the deployed models on. Using
    # ctx_dict-level fields (india_vix, oi_chg_pcr, etc.) here would be the
    # same schema mismatch that previously made this silently no-op.
    history_snapshot = getattr(engine_result, "oi_history_snapshot", None) if engine_result else None
    history_by_strike = {}
    if history_snapshot is not None and not history_snapshot.empty:
        # to_dict('records') instead of iterrows() — iterrows() re-wraps
        # every row in a pandas Series (type-coercion + overhead per row);
        # to_dict('records') converts the whole frame in one vectorized
        # pass, then the loop is just plain dict access below (same
        # .get() interface, so nothing downstream needs to change).
        for hrow in history_snapshot.to_dict("records"):
            history_by_strike[_to_int(hrow.get("StrikePrice", 0))] = hrow

    if use_virtual_oi and _VOI_COORDINATOR is not None and history_by_strike:
        for row in chain_rows:
            sk = row["strike"]
            hrow = history_by_strike.get(sk)
            if hrow is None:
                # No history row for this strike yet (e.g. brand-new strike) —
                # fall back to confirmed OI, same as the "coordinator absent" path.
                row["ceVirtualOI"], row["peVirtualOI"] = row["ceOI"], row["peOI"]
                row["ceVoiConf"], row["peVoiConf"] = 0.0, 0.0
                row["ceVoiDrift"], row["peVoiDrift"] = False, False
                continue

            real_features = {
                "ce_vol_delta":    hrow.get("CE_Volume_Delta", 0) or 0,
                "pe_vol_delta":    hrow.get("PE_Volume_Delta", 0) or 0,
                "ce_oi_delta_lag": hrow.get("CE_OI_Delta", 0) or 0,
                "pe_oi_delta_lag": hrow.get("PE_OI_Delta", 0) or 0,
                "ce_iv_delta":     hrow.get("CE_IV_Delta", 0) or 0,
                "pe_iv_delta":     hrow.get("PE_IV_Delta", 0) or 0,
            }

            for side, oi_key, dst_key in (
                ("CE", "ceOI", "ceVirtualOI"),
                ("PE", "peOI", "peVirtualOI"),
            ):
                symbol_key = f"{SYMBOL}_{sk}"
                confirmed_oi = row[oi_key]
                try:
                    frame = _VOI_COORDINATOR.dispatch_tick(
                        symbol=symbol_key,
                        side=side,
                        tick_features=real_features,
                        confirmed_oi=confirmed_oi,
                    )
                    if frame is None:
                        # No trained model for this side yet.
                        row[dst_key] = confirmed_oi
                        row[f"{side.lower()}VoiConf"]  = 0.0
                        row[f"{side.lower()}VoiDrift"] = False
                        continue

                    # Only treat this as an NSE refresh if confirmed OI actually
                    # moved since the estimator's last anchor — calling refresh
                    # every export cycle would reset the accumulator pointlessly
                    # and erase the gap-filling value entirely.
                    est = _VOI_COORDINATOR._estimators.get(f"{symbol_key}::{side}")
                    if est is not None and confirmed_oi != est.last_confirmed_oi:
                        _VOI_COORDINATOR.on_nse_refresh(symbol_key, side, confirmed_oi)

                    row[dst_key]                    = frame.virtual_oi_running
                    row[f"{side.lower()}VoiConf"]   = _r(frame.confidence_weight, 2)
                    row[f"{side.lower()}VoiDrift"]  = frame.drift_detected
                except Exception:
                    row[dst_key]                  = confirmed_oi
                    row[f"{side.lower()}VoiConf"]  = 0.0
                    row[f"{side.lower()}VoiDrift"] = False
    else:
        for row in chain_rows:
            row["ceVirtualOI"] = row["ceOI"]
            row["peVirtualOI"] = row["peOI"]
            row["ceVoiConf"]   = 0.0
            row["peVoiConf"]   = 0.0
            row["ceVoiDrift"]  = False
            row["peVoiDrift"]  = False


    # ── 6. Greeks rows ────────────────────────────────────────────────
    # Pulled out into a helper (was inline, master-only) so the exact same
    # calc can run against each extra_chains expiry below — previously only
    # the primary/default expiry ever got a "greeks" array, so switching
    # expiry on the frontend had no per-expiry Greeks data to swap to and
    # the Greeks tab / Greek-ATM (moneyness) chart just kept showing the
    # default expiry's numbers regardless of the dropdown selection.
    #
    # NOTE: this used to re-derive cDelta/cGamma/cTheta/cVega/netGEX by
    # reading r.get("ce_delta"/"ce_gamma"/"ce_theta"/"ce_vega", 0) off
    # `master` — but master (oi_analysis.build_master_table_nse's output)
    # never has those columns at all; Greeks are computed entirely
    # separately in engine.py's _build_greeks_table(), into its own
    # DataFrame (greeks_table), which never got wired to master. Every
    # r.get(...) call was silently returning 0, so ce_gamma (and every
    # other Greek) was 0 on every row, every tick — which made netGEX
    # exactly 0 everywhere too. That's what was driving:
    #   - Greeks-by-Moneyness chart (chart-legend.js) reading flat
    #   - Net GEX Profile bar chart always flat at 0
    #   - Net GEX ($B) stat frozen at 0.00, Gamma Flip Strike stuck at "--"
    #   - Institutional Simulator sliders visibly doing nothing (scaling
    #     0 by any ivRatio/vannaAdj/dealer-bias multiplier is still 0)
    # engine.py's greeks_table already has the real, correctly-computed
    # values (real Black-Scholes math against live ce_oi/pe_oi) and is
    # already exposed on ctx_dict (primary) / ex_ctx (per-expiry) as
    # "greeks_table" — so this now just converts that DataFrame's rows
    # into the shape the frontend expects, instead of recomputing (and
    # silently failing to recompute) anything.
    #
    # Also fixes a latent sign-convention mismatch: the old netGEX here
    # used (pe_oi - ce_oi), while engine.py's own enrich()/_build_greeks_
    # table() both use (ce_oi - pe_oi). Never visible before since
    # ce_gamma was always 0 regardless of order — now moot, since netGEX
    # is passed straight through from engine.py's own calculation.
    def _greeks_rows_from_table(greeks_table):
        if greeks_table is None or getattr(greeks_table, "empty", True):
            return []
        rows = []
        for r in greeks_table.to_dict("records"):
            rows.append({
                "strike": _to_int(r.get("Strike", r.get("strike", 0))),
                "iv":     _nullable_r(r.get("iv"), 2),
                "cDelta": _nullable_r(r.get("cDelta"), 4),
                "cGamma": _nullable_r(r.get("cGamma"), 4),
                "cTheta": _nullable_r(r.get("cTheta"), 4),
                "cVega":  _nullable_r(r.get("cVega"), 4),
                "pDelta": _nullable_r(r.get("pDelta"), 4),
                "pGamma": _nullable_r(r.get("pGamma"), 4),
                "pTheta": _nullable_r(r.get("pTheta"), 4),
                "pVega":  _nullable_r(r.get("pVega"), 4),
                "netGEX": _nullable_r(r.get("netGEX"), 4),
            })
        return rows

    lot_size = _to_int(ctx_dict.get("lot_size", 75))
    greeks_rows = _greeks_rows_from_table(ctx_dict.get("greeks_table"))

    # ── 7. OI velocity rows ───────────────────────────────────────────
    # _record_oi_snapshot() still runs every tick — _compute_vol_changes()
    # (ceVolChg/peVolChg, computed earlier above) depends on it. Only its
    # OTHER former consumer, the OI-velocity fallback (_compute_vel_rows),
    # is scrapped — the primary vel_df path (engine.py -> oi_analysis.
    # get_oi_velocity, off the parquet-backed history) works reliably now.
    _record_oi_snapshot(master)
    oi_velocity = [{"window": 5, "rows": []}, {"window": 15, "rows": []}, {"window": 30, "rows": []}]

    if engine_result is not None:
        vel_df = getattr(engine_result, "vel_df", None)
        if vel_df is not None and not vel_df.empty:
            for win_block in oi_velocity:
                w      = win_block["window"]
                subset = vel_df[vel_df["window"] == w] if "window" in vel_df.columns else vel_df
                rows   = []
                for vr in subset.to_dict("records"):
                    rows.append({
                        "strike": _to_int(vr.get("strike", 0)),
                        "ceNow":  _to_int(vr.get("ceNow",  vr.get("ce_oi",  0))),
                        "ceDOI":  _to_int(vr.get("ceDOI",  vr.get("ce_doi", 0))),
                        "ceLTP":  _r(vr.get("ceLTP",  vr.get("ce_ltp", 0)), 1),
                        "peNow":  _to_int(vr.get("peNow",  vr.get("pe_oi",  0))),
                        "peDOI":  _to_int(vr.get("peDOI",  vr.get("pe_doi", 0))),
                        "peLTP":  _r(vr.get("peLTP",  vr.get("pe_ltp", 0)), 1),
                        "signal": str(vr.get("signal", "")),
                    })
                win_block["rows"] = rows

    # ── 8. ATM CE/PE IV ───────────────────────────────────────────────
    atm_ce_iv = _nullable_r(ctx_dict.get("atm_ce_iv"), 2)
    atm_pe_iv = _nullable_r(ctx_dict.get("atm_pe_iv"), 2)
    if not atm_ce_iv or not atm_pe_iv:
        for row in chain_rows:
            if row["strike"] == atm_strike:
                if not atm_ce_iv: atm_ce_iv = row["ceIV"]
                if not atm_pe_iv: atm_pe_iv = row["peIV"]
                break

    # ── 9a. Build multi-expiry chains dict ────────────────────────────
    # chains[expiry_str] = list of chain rows  (CURRENT chain always present)
    chains_by_expiry = {str(EXPIRY): chain_rows}

    if extra_chains:
        for exp_str, tup in extra_chains.items():
            try:
                ex_df_clean, ex_master, ex_ctx, ex_dte = tup
                # Build bid/ask map + chain rows for this expiry (shared helpers)
                ex_ba  = _build_bid_ask_map(ex_df_clean)
                ex_atm = _to_int(ex_ctx.get("atm", 0))
                if ex_atm == 0:
                    # Vectorized ATM lookup — was a Python iterrows() pass
                    # building a list just to find the closest strike.
                    ex_spot = _r(ex_ctx.get("spot", spot), 2)
                    valid_strikes = ex_master.loc[ex_master["strike"] > 0, "strike"]
                    if not valid_strikes.empty and ex_spot > 0:
                        ex_atm = _to_int(
                            valid_strikes.iloc[(valid_strikes - ex_spot).abs().argmin()]
                        )
                ex_capital_map = _build_capital_map(compute_footprint_score(ex_ctx.get("capital_metrics")))
                ex_rows = _build_chain_rows(ex_master, ex_atm, ex_ba, ex_capital_map)
                # extra_chains entries don't run the volume-change snapshot
                # pass (that's only computed for the primary chain) — keep
                # the fields present so the frontend shape stays consistent.
                for row in ex_rows:
                    row["ceVolChg"] = 0
                    row["peVolChg"] = 0
                chains_by_expiry[exp_str] = ex_rows

                # --- per-expiry summary metrics (atm, walls, pcr, iv, straddle) ---
                # Stored as chainMeta[expiry_str] for the HTML to use when switching
                # Vectorized sums — was two more full iterrows() passes over
                # the same ex_master DataFrame (on top of the ATM lookup and
                # _build_chain_rows above), 4 total passes per extra expiry
                # per tick for no reason.
                ex_ce_oi = _to_int(ex_master["ce_oi"].fillna(0).sum())
                ex_pe_oi = _to_int(ex_master["pe_oi"].fillna(0).sum())
                atm_row  = next((r for r in ex_rows if r["atm"]), None)
                # Per-expiry Greeks (same source as the primary chain's
                # "greeks" array) — also used below as the fallback source
                # for the four ATM Greeks scalars.
                ex_greeks = _greeks_rows_from_table(ex_ctx.get("greeks_table"))
                ex_greeks_atm = next((g for g in ex_greeks if g["strike"] == ex_atm), None)
                chains_by_expiry[f"__meta__{exp_str}"] = {
                    "expiry":      exp_str,
                    "dte":         _to_int(ex_dte),
                    "atm":         ex_atm,
                    "atmCeIV":     atm_row["ceIV"]  if atm_row else None,
                    "atmPeIV":     atm_row["peIV"]  if atm_row else None,
                    "atmIV":       _nullable_r((atm_row["ceIV"] + atm_row["peIV"]) / 2, 2)
                                   if atm_row and atm_row["ceIV"] is not None and atm_row["peIV"] is not None else None,
                    "straddle":    _r((atm_row["ceLTP"] + atm_row["peLTP"]), 2) if atm_row else 0,
                    "ceWall":      _to_int(ex_ctx.get("ce_wall",  0)),
                    "peWall":      _to_int(ex_ctx.get("pe_wall",  0)),
                    "maxPain":     _to_int(ex_ctx.get("max_pain", 0)),
                    "totalPCR":    _r(ex_pe_oi / ex_ce_oi, 2) if ex_ce_oi else 0,
                    # Per-expiry Greeks — same shape/calc as the primary
                    # chain's "greeks" array. Without this the Greeks tab
                    # and Greek-ATM (moneyness) chart had nothing to switch
                    # to and silently kept showing the default expiry.
                    "greeks":      ex_greeks,
                    # ATM Greeks scalars (the "ATM Greeks" card) — prefer
                    # this expiry's own context value if the caller set one
                    # (mirrors how the primary payload reads atm_delta/etc
                    # from ctx_dict), otherwise fall back to this expiry's
                    # own ATM-strike row from ex_greeks so the card is never
                    # just frozen on the default expiry's numbers.
                    "atmDelta":    _r(ex_ctx.get("atm_delta", ex_greeks_atm["cDelta"] if ex_greeks_atm else 0.5), 4),
                    "atmGamma":    _r(ex_ctx.get("atm_gamma", ex_greeks_atm["cGamma"] if ex_greeks_atm else 0.0), 4),
                    "atmTheta":    _r(ex_ctx.get("atm_theta", ex_greeks_atm["cTheta"] if ex_greeks_atm else 0.0), 4),
                    "atmVega":     _r(ex_ctx.get("atm_vega",  ex_greeks_atm["cVega"]  if ex_greeks_atm else 0.0), 4),
                }
            except Exception as _ec_err:
                logger.warning(f"[export_dashboard_json] extra_chains build failed for {exp_str}: {_ec_err}")

    # ── 9. Panels ──────────────────────────────────────────────────────
    # BUGFIX: this used to be `if "expiry_label" not in ctx_dict: ...`,
    # i.e. a set-once guard. ctx_dict is a long-lived object mutated in
    # place across ticks (see the ctx_dict["atm"] note above), so that
    # guard meant expiry_label was written ONE time — on whichever tick
    # first ran this function — and never touched again, even though
    # EXPIRY (the live front-month, passed in fresh on every call from
    # the option-chain feed) keeps rolling forward as contracts expire.
    # Net effect: the option chain correctly moved from e.g. "24-Jun" to
    # "14-Jul" as the week rolled, but every strategy leg built off
    # ctx_dict["expiry_label"] (see _build_strategies) stayed pinned to
    # whatever expiry happened to be live the first time this ran —
    # producing orders against an already-expired contract that the
    # backend engine then hard-rejects. expiry_label must track EXPIRY
    # on every export, not just the first one.
    ctx_dict["expiry_label"] = str(EXPIRY)

    signals    = _build_signals(ctx_dict, engine_result)
    strategies = _build_strategies(ctx_dict, engine_result, chain_rows=chain_rows)
    risk       = _build_risk(ctx_dict, engine_result)

    # ── 9c. Chain-wide capital-weighted rollup (Executive Card) ────────
    # oi.capital_metrics.compute_chain_metrics() off the same capital_metrics
    # DataFrame chain_rows was built from above — one dict, not a 400-strike
    # table. {} when unavailable so the frontend gets a consistently-shaped
    # (empty) object instead of a missing key.
    capital_df_for_rollup = ctx_dict.get("capital_metrics")
    capital_summary = (
        compute_chain_metrics(capital_df_for_rollup)
        if capital_df_for_rollup is not None and not capital_df_for_rollup.empty
        else {}
    )

    # ── 9d. Smart Money Summary (Executive Card) ────────────────────────
    # Rollup of Market Regime + the capital_summary just computed above —
    # see analytics/smart_money_summary.py for why footprintScore isn't
    # included yet.
    smart_money_summary = compute_smart_money_summary(
        market_regime=ctx_dict.get("market_regime") or {},
        capital_summary=capital_summary,
        fut_oi_chg_pct=ctx_dict.get("fut_oi_chg_pct", 0.0),
    )

    # ── 9e. Capital vs Futures OI / Capital Confirmation / Futures-Options
    # Divergence (Phase B) ───────────────────────────────────────────────
    # All three reuse market_regime + capital_summary above and fut_oi_chg
    # off ctx_dict — no new per-strike computation. volume_ratio is a
    # lightweight total-volume/total-OI read straight off the same
    # capital_df_for_rollup used for capital_summary, not a new fetch.
    _regime_label = (ctx_dict.get("market_regime") or {}).get("regime", "Indeterminate")
    capital_vs_futures = classify_capital_vs_futures(
        net_capital_flow=capital_summary.get("net_capital_flow", 0.0),
        fut_oi_chg=ctx_dict.get("fut_oi_chg", 0.0),
    ) if capital_summary else {}

    _volume_ratio = None
    if capital_df_for_rollup is not None and not capital_df_for_rollup.empty:
        _tot_vol = float(capital_df_for_rollup["ce_volume"].sum(skipna=True) +
                          capital_df_for_rollup["pe_volume"].sum(skipna=True))
        _tot_oi = float(capital_df_for_rollup["ce_oi"].sum(skipna=True) +
                         capital_df_for_rollup["pe_oi"].sum(skipna=True))
        _volume_ratio = (_tot_vol / _tot_oi) if _tot_oi > 0 else None

    capital_confirmation = compute_capital_confirmation(
        net_capital_flow=capital_summary.get("net_capital_flow", 0.0),
        regime=_regime_label,
        price_chg_pct=ctx_dict.get("market_regime", {}).get("price_chg_pct", 0.0),
        volume_ratio=_volume_ratio,
    ) if capital_summary else {}

    futures_options_divergence = detect_futures_options_divergence(
        regime=_regime_label,
        net_capital_flow=capital_summary.get("net_capital_flow", 0.0),
    ) if capital_summary else {}

    # ── 9f. Institutional Footprint Score ranking + Capital Concentration
    # (Phase C) ──────────────────────────────────────────────────────────
    # Per-strike footprintScore itself is already attached to each chain
    # row above (see the capital_map wrapping at "4. Chain rows"); this is
    # just the top-N ranking + concentration % for the Executive card,
    # reusing that same footprint-scored df rather than recomputing it a
    # second time.
    _footprint_df = compute_footprint_score(capital_df_for_rollup) if capital_df_for_rollup is not None else None
    footprint_ranked = rank_footprint_strikes(_footprint_df, top_n=8) if _footprint_df is not None else []
    capital_concentration = compute_capital_concentration(capital_df_for_rollup, top_n=5) \
        if capital_df_for_rollup is not None else {}

    # ── 10. Full payload ──────────────────────────────────────────────
    payload = {
        "dataContract": {
            "schemaVersion": "1.0.0",
            "rowIdentity": ["symbol", "expiry", "strike"],
            "optionSideNamespaces": ["ce", "pe"],
            "units": {
                "oi": "lot_scaled_underlying_quantity",
                "changeOi": "lot_scaled_underlying_quantity",
                "volume": "contracts",
                "price": "INR_per_underlying_unit",
                "iv": "percent_points",
                "delta": "underlying_units_per_price_unit",
                "gamma": "delta_change_per_price_unit",
                "theta": "INR_per_day_per_lot",
                "vega": "INR_per_volatility_point_per_lot",
                "capital": "INR",
            },
            "nullability": {
                "missingMarketValue": None,
                "unverifiedGreekExposure": None,
                "zeroMeaning": "observed_or_computed_zero",
            },
            "provenance": {
                "chain": "exchange_or_broker_market_feed",
                "greeks": "canonical_analytics_engine",
                "capital": "oi.capital_metrics",
                "decision": "decision.DecisionEngine",
            },
            "freshness": {
                "observedAtField": "lastUpdated",
                "transportStateOwnedBy": "websocket_envelope",
            },
        },
        "symbol":        str(SYMBOL),
        "spot":          spot,
        "spotChange":    _r(ctx_dict.get("spot_change",  0), 2),
        "spotChgPct":    _r(ctx_dict.get("spot_chg_pct", 0), 2),
        "spotBias":      str(ctx_dict.get("bias",        "Neutral")),
        # EQ is always the analytical source. Futures remain a separate
        # quote and confirmation input.
        "priceSource":   "EQ",
        # Near-month futures expiry used when price_source="FUT" (empty
        # string when EQ or unset) — same reasoning as priceSource above:
        # the frontend needs this to label which contract the FUT price
        # came from, not just that it's a FUT price.
        "futuresExpiry": str(futures_expiry) if futures_expiry else "",
        "expiry":        str(EXPIRY),
        "expiryDates":   expiry_dates or [],   # <-- NEW: full list
        "dte":           _to_int(dte),
        "atm":           atm_strike,

        "future":        _r(spot + _r(ctx_dict.get("basis", 0), 2), 2),
        "basis":         _r(ctx_dict.get("basis", 0), 2),

        "maxPain":       _to_int(ctx_dict.get("max_pain",      0)),
        "maxPainDist":   _r(ctx_dict.get("max_pain_dist",      0), 2),
        "ceWall":        _to_int(ctx_dict.get("ce_wall",        0)),
        "peWall":        _to_int(ctx_dict.get("pe_wall",        0)),

        "totalPCR":      _r(ctx_dict.get("total_pcr",    1.0), 2),
        "oiChgPCR":      _r(ctx_dict.get("oi_chg_pcr",   0.0), 2),
        "pcrSentiment":  str(ctx_dict.get("pcr_sentiment", "Balanced")),

        # ── capital-weighted chain rollup (Executive Card / Smart Money) ──
        # PE/CE ratio weighted by premium locked rather than raw OI count —
        # compare against totalPCR above; divergence between the two is
        # itself a signal (see oi.capital_metrics.compute_chain_metrics
        # docstring). netGammaExposureCapital is CE-minus-PE (same
        # differencing convention as the existing raw-OI netGEX), NOT a sum.
        # capitalCeWallStrike/capitalPeWallStrike are the highest-premium
        # strikes and can differ from ceWall/peWall above (highest raw OI) —
        # frontend should label the two distinctly, not overwrite one with
        # the other.
        "capitalPCR":            _r(capital_summary.get("capital_pcr", 0.0), 2),
        "netPremiumLocked":      _r(capital_summary.get("net_premium_locked", 0.0), 2),
        "netCapitalFlow":        _r(capital_summary.get("net_capital_flow", 0.0), 2),
        "netGammaExposureCapital": _nullable_r(capital_summary.get("net_gamma_exposure"), 2),
        "netDeltaExposureCapital": _nullable_r(capital_summary.get("net_delta_exposure"), 2),
        "totalNotionalExposureCapital": _r(
            capital_summary.get("total_ce_notional_exposure", 0.0)
            + capital_summary.get("total_pe_notional_exposure", 0.0), 2
        ),
        "totalPremiumTurnoverCapital": _r(
            capital_summary.get("total_ce_premium_turnover", 0.0)
            + capital_summary.get("total_pe_premium_turnover", 0.0), 2
        ),
        "totalPremiumLockedCapital": _r(
            capital_summary.get("total_ce_premium_locked", 0.0)
            + capital_summary.get("total_pe_premium_locked", 0.0), 2
        ),
        "capitalCeWallStrike":   _to_int(capital_summary.get("ce_capital_wall_strike") or 0),
        "capitalPeWallStrike":   _to_int(capital_summary.get("pe_capital_wall_strike") or 0),

        "atmIV":         _r(ctx_dict.get("base_iv", DEFAULT_BASE_IV) * 100, 2),
        "atmCeIV":       atm_ce_iv,
        "atmPeIV":       atm_pe_iv,
        "atmSkew":       _r(ctx_dict.get("atm_skew", 0.0), 2),
        "ivRank":        _r(ctx_dict.get("iv_rank",  35.0), 2),
        "hv30":          _r(ctx_dict.get("hv30",     15.0), 1),
        "indiaVix":      _r(ctx_dict.get("india_vix",14.0), 1),
        "indiaVixChgPct": _r(ctx_dict.get("india_vix_chg_pct", 0.0), 2),
        "vixRegime":     str(ctx_dict.get("vix_regime", "Normal")),

        "futSignal":     str(ctx_dict.get("fut_signal",  "Neutral")),
        "trapWarn":      str(ctx_dict.get("trap_warn",   "None")),
        "compositeBias": str(ctx_dict.get("bias",        "Neutral")),

        "callPremium":   _r(ctx_dict.get("ce_premium",  0.0), 2),
        "putPremium":    _r(ctx_dict.get("pe_premium",  0.0), 2),
        "straddle":      _r(ctx_dict.get("ce_premium",  0.0) + ctx_dict.get("pe_premium", 0.0), 2),
        "atmDelta":      _r(ctx_dict.get("atm_delta",   0.5),  4),
        "atmGamma":      _r(ctx_dict.get("atm_gamma",   0.0),  4),
        "atmTheta":      _r(ctx_dict.get("atm_theta",   0.0),  4),
        "atmVega":       _r(ctx_dict.get("atm_vega",    0.0),  4),

        "refreshTime":   refresh_time,
        "lastUpdated":   last_updated,   # <-- NEW: full ISO timestamp

        "signals":       signals,
        "strategies":    strategies,
        "risk":          risk,

        "oiVelocity":    oi_velocity,
        "greeks":        greeks_rows,
        "chain":         chain_rows,
        # ── multi-expiry chain store ──────────────────────────────────
        # chains[expiry_str] → array of chain rows (same shape as "chain")
        # chainMeta[expiry_str] → atm, dte, walls, pcr, iv for that expiry
        "chains":        {k: v for k, v in chains_by_expiry.items() if not k.startswith("__meta__")},
        "chainMeta":     {k[8:]: v for k, v in chains_by_expiry.items() if k.startswith("__meta__")},
        # ── Top Drivers/Draggers (Dashboard's exec-grid "Top Movers" card) ──
        # [] for symbols with no matching NSE index basket (e.g. BSE symbols).
        "contributors":  contributors or [],
        # ── FII/DII participant positioning sentiment (display only) ──
        # Lagged to the prior trading day's EOD file (see fii_dii_sentiment.py
        # docstring) — never same-day, to avoid lookahead. {} if unavailable
        # (fetch hasn't run yet, module missing, or no file for that date).
        "fiiDiiSentiment": _get_cached_fii_dii_sentiment() or {},
        # ── Combined cash+F&O FII/DII bias summary (display only) ──
        # Same source ws_server_live.py's bridge_loop() computes for the
        # /dashboard-relay feed (fiidii-report.js's fdRenderBias) — cached
        # here on the same once/day cadence so the main dashboard's FII/DII
        # summary card can show it without needing that relay connection.
        "fiiDiiBias": _get_cached_fii_dii_bias() or {},
        # ── Market Regime (Price vs Futures OI) — Executive card ──────────
        # Tick-fresh (not day-cached like fiiDiiBias above) — see
        # analytics/market_regime.py. {} only if engine_result somehow ran
        # without this field (defensive; market_regime always builds one).
        "marketRegime": ctx_dict.get("market_regime") or {},
        "futOi":        _r(ctx_dict.get("fut_oi", 0.0), 0),
        "futOiChg":     _r(ctx_dict.get("fut_oi_chg", 0.0), 0),
        "futOiChgPct":  _r(ctx_dict.get("fut_oi_chg_pct", 0.0), 2),
        "smartMoneySummary": smart_money_summary,
        "capitalVsFutures": capital_vs_futures,
        "capitalConfirmation": capital_confirmation,
        "futuresOptionsDivergence": futures_options_divergence,
        "footprintRanked": footprint_ranked,
        "capitalConcentration": capital_concentration,
        # ── Index ticker strip data (NIFTY/BANKNIFTY/MIDCPNIFTY/SENSEX) ──
        # Fetched from NSE allIndices endpoint + BSE getScripHeaderData when relevant
        "allIndices": all_indices or [],
        # ── Top-bar symbol picker options ──────────────────────────────
        # {"indices": [...], "stocks": [...]} covering EVERY NSE/BSE
        # underlying with live F&O contracts (not just the 6-symbol
        # COMMON_SYMBOLS list dashboard.js used to hardcode) — see
        # smartapi_client.get_fno_underlyings() / renderSymbolOptions()
        # in chain-views.js.
        "fnoSymbols": _get_fno_symbols(),
    }

    # ── 10b. Simulator support fields (V51Pro) ──────────────────────────
    # The Institutional F&O Simulator section reads d.ctx.{spot,atm,baseIv}
    # and d.volOiRatios.{strike}. Neither existed in the payload before —
    # these are pure aliases/derivations off data already computed above,
    # so nothing else changes.
    payload["ctx"] = {
        "spot":   payload["spot"],
        "atm":    payload["atm"],
        "baseIv": payload["atmIV"],
    }

    vol_oi_ratios = {}
    for row in chain_rows:
        sk = row["strike"]
        ce_oi  = row.get("ceOI", 0) or 0
        pe_oi  = row.get("peOI", 0) or 0
        ce_vol = row.get("ceVol", 0) or 0
        pe_vol = row.get("peVol", 0) or 0
        vol_oi_ratios[str(sk)] = {
            "ce":     _r(ce_vol / ce_oi, 3) if ce_oi else 0.0,
            "pe":     _r(pe_vol / pe_oi, 3) if pe_oi else 0.0,
            "ce_vol": ce_vol,
            "pe_vol": pe_vol,
        }
    payload["volOiRatios"] = vol_oi_ratios

    # ── 11. ExpiryContext (current / near / monthly / far) ─────────────────
    try:
        em = make_expiry_manager(expiry_dates or [EXPIRY])
        # Capture the actually-fetched expiry/dte BEFORE the update below —
        # ExpiryManager's own "current" bucket is computed independently as
        # the nearest calendar expiry from expiry_dates, with no awareness
        # of which expiry EXPIRY was actually resolved to this tick (e.g.
        # after a manual dropdown switch to a non-nearest expiry). Letting
        # its output overwrite payload["expiry"]/["dte"] unconditionally
        # silently reverted every manual expiry switch back to the nearest
        # expiry on every tick, even though the correct chain had already
        # been fetched and everything else in the payload was correct.
        _actual_expiry = payload["expiry"]
        _actual_dte = payload.get("dte")
        payload.update(em.to_json_payload())   # adds expiryContext; also overwrites expiry/dte/expiryDates — restored below
        payload["expiry"] = _actual_expiry
        if _actual_dte is not None:
            payload["dte"] = _actual_dte
    except Exception as _em_err:
        logger.warning(f"[export_dashboard_json] ExpiryManager failed ({_em_err}) — expiryContext omitted")
        payload["expiryDates"]   = expiry_dates or [EXPIRY]
        payload["expiryContext"] = None

    # ── 12. Decision block ──────────────────────────────────────────────────
    if engine_result is not None:
        try:
            ctx_for_decision = engine_result.to_ctx_dict() if hasattr(engine_result, "to_ctx_dict") else ctx_dict
            ctx_for_decision = dict(ctx_for_decision or {})
            ctx_for_decision["_decision_timestamp"] = last_updated
            ctx_for_decision["_state_version"] = f"{payload.get('symbol', '')}:{payload.get('expiry', '')}:{last_updated}"
            payload["decision"] = DecisionEngine().evaluate(engine_result, ctx_for_decision).to_dict()
            # Persist this tick's decision so backtest/replay.py has
            # ground-truth history to replay auto_executor.py against.
            # See backtest/snapshot_logger.py's module docstring for why
            # this has to run from here (live) rather than being
            # reconstructed after the fact. Never raises.
            try:
                from backtest.snapshot_logger import log_decision_snapshot
                log_decision_snapshot(engine_result, payload["decision"])
            except Exception as _snap_err:
                logger.warning(f"[export_dashboard_json] decision snapshot logging failed ({_snap_err})")
        except Exception as _de_err:
            logger.warning(f"[export_dashboard_json] DecisionEngine failed ({_de_err}) — decision block omitted")
            payload["decision"] = {
                "decisionTimestamp": last_updated,
                "stateVersion": f"{payload.get('symbol', '')}:{payload.get('expiry', '')}:{last_updated}",
                "stale": False, "degraded": True, "evidenceCoverage": 0,
                "missingInputs": ["decision_engine"], "contributors": [],
                "bias": "NEUTRAL", "biasStrength": "WEAK", "confidence": 0,
                "conflictFlag": False, "action": "Decision engine error",
                "actionType": "WAIT", "suggestedStrike": None,
                "suggestedStrategy": "", "executeRecommended": False,
                "strategyCaution": "Decision engine unavailable",
                "activeSignals": [{"text": str(_de_err), "severity": "warn"}],
                "verdicts": {}, "oiAnnotations": {}, "autoStrategy": {},
                "tradeGrade": "", "riskWarning": "Decision engine unavailable",
                "importantLevels": {}, "_debug": {"error": str(_de_err)},
            }


    # orjson is ~5-10× faster than stdlib json for the dashboard payload
    # and is already a hard dependency of ws_server_live.py. Fall back to
    # json.dump if orjson isn't available OR if a non-serializable type
    # still sneaks into the payload after the default coercer runs
    # (engine/decision paths routinely leave numpy scalars / Timestamps).
    _write_dashboard_json(out_path, payload)

    vel_counts = [len(b["rows"]) for b in oi_velocity]
    vol_count = len([r for r in chain_rows if r.get("ceVolChg") != 0 or r.get("peVolChg") != 0])
    has_decision = "decision" in payload
    logger.info(f"[export_dashboard_json] JSON exported → {out_path}  ({len(chain_rows)} strikes, "
                f"{len(strategies)} strategies, {len(signals)} signals, "
                f"vel rows 5m/15m/30m: {vel_counts[0]}/{vel_counts[1]}/{vel_counts[2]}, "
                f"vol changes: {vol_count} strikes, decision={has_decision})")
    return payload
