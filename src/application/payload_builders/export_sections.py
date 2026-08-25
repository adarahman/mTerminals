"""Bounded sub-builders for export_dashboard_json.

These were inline blocks in application/dashboard/serializer.py's
export_dashboard_json(); they are moved here verbatim (same logic, same
inputs/outputs) so the main export function reads as orchestration. The
heavy per-panel builders (chain rows, signals, decision, greeks, …) already
live in application/payload_builders/* — these helpers cover the remaining
self-contained payload sections.
"""
import logging

from application.payload_builders.common import (
    integer as _to_int,
    nullable_rounded_number as _nullable_r,
    rounded_number as _r,
)
from application.payload_builders.market_rows import (
    build_bid_ask_map as _build_bid_ask_map,
    build_capital_map as _build_capital_map,
    build_chain_rows as _build_chain_rows,
)
from application.payload_builders.greeks import (
    build_greeks_rows as _greeks_rows_from_table,
)
from oi.footprint_score import compute_footprint_score
from market.expiry.service import make_expiry_manager

logger = logging.getLogger(__name__)


def build_extra_chains(extra_chains, EXPIRY, spot):
    """Build per-expiry chain rows + summary metadata for the multi-expiry
    store. Returns a dict keyed by expiry string (chain rows) and
    ``__meta__{expiry}`` (summary metrics); the caller merges it into
    chains_by_expiry. Mirrors the inline block previously in
    export_dashboard_json (lines 199-269)."""
    additions = {}
    for exp_str, tup in extra_chains.items():
        try:
            ex_df_clean, ex_master, ex_ctx, ex_dte = tup
            ex_ba = _build_bid_ask_map(ex_df_clean)
            ex_atm = _to_int(ex_ctx.get("atm", 0))
            if ex_atm == 0:
                ex_spot = _r(ex_ctx.get("spot", spot), 2)
                valid_strikes = ex_master.loc[ex_master["strike"] > 0, "strike"]
                if not valid_strikes.empty and ex_spot > 0:
                    ex_atm = _to_int(valid_strikes.iloc[(valid_strikes - ex_spot).abs().argmin()])
            ex_capital_map = _build_capital_map(compute_footprint_score(ex_ctx.get("capital_metrics")))
            ex_rows = _build_chain_rows(ex_master, ex_atm, ex_ba, ex_capital_map)
            # extra_chains entries don't run the volume-change snapshot
            # pass (that's only computed for the primary chain) — keep
            # the fields present so the frontend shape stays consistent.
            for row in ex_rows:
                row["ceVolChg"] = 0
                row["peVolChg"] = 0
            additions[exp_str] = ex_rows

            # --- per-expiry summary metrics (atm, walls, pcr, iv, straddle) ---
            ex_ce_oi = _to_int(ex_master["ce_oi"].fillna(0).sum())
            ex_pe_oi = _to_int(ex_master["pe_oi"].fillna(0).sum())
            atm_row = next((r for r in ex_rows if r["atm"]), None)
            # Per-expiry Greeks (same source as the primary chain's
            # "greeks" array) — also used below as the fallback source
            # for the four ATM Greeks scalars.
            ex_greeks = _greeks_rows_from_table(ex_ctx.get("greeks_table"))
            ex_greeks_atm = next((g for g in ex_greeks if g["strike"] == ex_atm), None)
            additions[f"__meta__{exp_str}"] = {
                "expiry": exp_str,
                "dte": _to_int(ex_dte),
                "atm": ex_atm,
                "atmCeIV": atm_row["ceIV"] if atm_row else None,
                "atmPeIV": atm_row["peIV"] if atm_row else None,
                "atmIV": _nullable_r((atm_row["ceIV"] + atm_row["peIV"]) / 2, 2)
                          if atm_row and atm_row["ceIV"] is not None and atm_row["peIV"] is not None else None,
                "straddle": _r((atm_row["ceLTP"] + atm_row["peLTP"]), 2) if atm_row else 0,
                "ceWall": _to_int(ex_ctx.get("ce_wall", 0)),
                "peWall": _to_int(ex_ctx.get("pe_wall", 0)),
                "maxPain": _to_int(ex_ctx.get("max_pain", 0)),
                "totalPCR": _r(ex_pe_oi / ex_ce_oi, 2) if ex_ce_oi else 0,
                # Per-expiry Greeks — same shape/calc as the primary
                # chain's "greeks" array. Without this the Greeks tab
                # and Greek-ATM (moneyness) chart had nothing to switch
                # to and silently kept showing the default expiry.
                "greeks": ex_greeks,
                # ATM Greeks scalars (the "ATM Greeks" card) — prefer
                # this expiry's own context value if the caller set one
                # (mirrors how the primary payload reads atm_delta/etc
                # from ctx_dict), otherwise fall back to this expiry's
                # own ATM-strike row from ex_greeks so the card is never
                # just frozen on the default expiry's numbers.
                "atmDelta": _r(ex_ctx.get("atm_delta", ex_greeks_atm["cDelta"] if ex_greeks_atm else 0.5), 4),
                "atmGamma": _r(ex_ctx.get("atm_gamma", ex_greeks_atm["cGamma"] if ex_greeks_atm else 0.0), 4),
                "atmTheta": _r(ex_ctx.get("atm_theta", ex_greeks_atm["cTheta"] if ex_greeks_atm else 0.0), 4),
                "atmVega": _r(ex_ctx.get("atm_vega", ex_greeks_atm["cVega"] if ex_greeks_atm else 0.0), 4),
            }
        except Exception as _ec_err:
            logger.warning(f"[export_dashboard_json] extra_chains build failed for {exp_str}: {_ec_err}")
    return additions


def build_vol_oi_ratios(chain_rows):
    """Per-strike volume/OI ratios (Simulator support field)."""
    vol_oi_ratios = {}
    for row in chain_rows:
        sk = row["strike"]
        ce_oi = row.get("ceOI", 0) or 0
        pe_oi = row.get("peOI", 0) or 0
        ce_vol = row.get("ceVol", 0) or 0
        pe_vol = row.get("peVol", 0) or 0
        vol_oi_ratios[str(sk)] = {
            "ce": _r(ce_vol / ce_oi, 3) if ce_oi else 0.0,
            "pe": _r(pe_vol / pe_oi, 3) if pe_oi else 0.0,
            "ce_vol": ce_vol,
            "pe_vol": pe_vol,
        }
    return vol_oi_ratios


def apply_expiry_context(payload, expiry_dates, EXPIRY):
    """Attach ExpiryManager context (current/near/monthly/far) to payload.

    Mirrors the inline block previously in export_dashboard_json (lines
    564-585): captures the actually-fetched expiry/dte before Em's update
    overwrites them, then restores, so manual expiry switches survive the
    per-tick recompute.
    """
    try:
        em = make_expiry_manager(expiry_dates or [EXPIRY])
        # Capture the actually-fetched expiry/dte BEFORE the update below —
        # ExpiryManager's own "current" bucket is computed independently as
        # the nearest calendar expiry from expiry_dates, with no awareness
        # of which expiry EXPIRY was actually resolved to this tick.
        _actual_expiry = payload["expiry"]
        _actual_dte = payload.get("dte")
        payload.update(em.to_json_payload())
        payload["expiry"] = _actual_expiry
        if _actual_dte is not None:
            payload["dte"] = _actual_dte
    except Exception as _em_err:
        logger.warning(f"[export_dashboard_json] ExpiryManager failed ({_em_err}) — expiryContext omitted")
        payload["expiryDates"] = expiry_dates or [EXPIRY]
        payload["expiryContext"] = None
    return payload
