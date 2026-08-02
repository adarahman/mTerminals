"""Sub-signal scorers, verdict text builders, and the trap detector.

Split out of decision_engine.py's DecisionEngine class (Step: decision/
package split). Pure move — every function here was a `self._foo(...)`
method that only used `self` to call sibling methods or read the
`_VOL_CONFIRM_*` class constants, never actual instance state (a fresh
DecisionEngine() is created every tick, so there was never any state to
carry between calls anyway). Converted to plain module-level functions;
no scoring logic, thresholds, or output changed.
"""

from __future__ import annotations
import time

import pandas as pd

from storage.caches import RollingWindow
from oi.pricing import DEFAULT_BASE_IV

from decision.types import T, ActiveSignal, DecisionResult

# DecisionEngine.evaluate() runs on a fresh DecisionEngine() instance every
# tick (see mTerminals_json.export_dashboard_json()), so per-instance state
# never survives between polls. India VIX is a single market-wide reading
# regardless of symbol/expiry, so one process-level history is correct even
# though ws_server_live.py runs one process per --symbol.
_VIX_HISTORY = RollingWindow(max_age_seconds=T.IV_CRUSH_MAX_AGE_SECONDS)

# ── Volume confirmation constants ─────────────────────────────────────────
# Vol/OI ratio thresholds — classify conviction level behind an OI move.
# A high ratio means many contracts traded relative to open interest:
# strong conviction (new money, not just rollover).
_VOL_CONFIRM_STRONG   = 1.0    # vol/OI ≥ 1.0 → full conviction  (multiplier 1.3)
_VOL_CONFIRM_MODERATE = 0.30   # vol/OI ≥ 0.3 → confirmed         (multiplier 1.15)
_VOL_CONFIRM_WEAK     = 0.05   # vol/OI ≥ 0.05 → marginal          (multiplier 1.0)
# Below _VOL_CONFIRM_WEAK → price-only adjustment, penalise conviction (×0.75)


def _vol_multiplier(vol_oi: float) -> float:
    """Convert a vol/OI ratio into a [0.75 – 1.30] conviction multiplier."""
    if vol_oi >= _VOL_CONFIRM_STRONG:
        return 1.30
    if vol_oi >= _VOL_CONFIRM_MODERATE:
        return 1.15
    if vol_oi >= _VOL_CONFIRM_WEAK:
        return 1.00
    return 0.75   # OI moved but almost no volume → likely roll / adjustment


def score_pcr(pcr: float) -> float:
    """
    PCR = PE_OI / CE_OI
    HIGH → put writers dominant → bulls expect floor to hold → BULLISH (+)
    LOW  → call writers dominant → bears cap upside → BEARISH (-)
    """
    if pcr >= T.PCR_BULL_EXTREME: return +1.0
    if pcr >= T.PCR_BULL:         return +0.65
    if pcr <= T.PCR_BEAR_EXTREME: return -1.0
    if pcr <= T.PCR_BEAR:         return -0.65
    # linear across neutral band [0.90 – 1.10]
    return (pcr - 1.0) / (T.PCR_NEUTRAL_HI - 1.0) * 0.35


def score_engine_bias(bias_str: str) -> float:
    b = bias_str.lower()
    if "strong bull" in b: return +1.0
    if "bull"        in b: return +0.60
    if "strong bear" in b: return -1.0
    if "bear"        in b: return -0.60
    return 0.0


def score_futures(fut_signal: str, basis: float) -> float:
    """Long buildup / Short covering → bullish; Short buildup / Long unwinding → bearish"""
    sig = fut_signal.lower()
    if   "long buildup"     in sig: score =  0.80
    elif "short covering"   in sig: score =  0.60
    elif "short buildup"    in sig: score = -0.80
    elif "long unwinding"   in sig: score = -0.60
    else:                           score =  0.0
    # Basis premium/discount as secondary confirmation (±0.1 nudge)
    if   basis > 30:  score = min( 1.0, score + 0.10)
    elif basis < -30: score = max(-1.0, score - 0.10)
    return score


def score_vix(vix: float, out: DecisionResult) -> str:
    if vix <= 0:
        out.verdicts["vix"] = "VIX unavailable"
        return "NORMAL"

    if vix < T.VIX_LOW:
        tag = "LOW"
        out.verdicts["vix"] = (f"{vix:.1f} — Complacency zone · "
                               f"premium sellers have structural edge")
        out.active_signals.append(ActiveSignal(
            f"VIX {vix:.1f} (low) — sell-premium regime: straddle / strangle edge", "ok", 5))

    elif vix < T.VIX_NORMAL:
        tag = "NORMAL"
        out.verdicts["vix"] = f"{vix:.1f} — Normal · no vol regime edge"

    elif vix < T.VIX_HIGH:
        tag = "HIGH"
        out.verdicts["vix"] = (f"{vix:.1f} — Elevated fear · "
                               f"reduce naked short gamma; use spreads")
        out.active_signals.append(ActiveSignal(
            f"VIX {vix:.1f} (elevated) — hedge before selling premium", "warn", 4))

    elif vix < T.VIX_PANIC:
        tag = "VERY_HIGH"
        out.verdicts["vix"] = (f"{vix:.1f} — High fear · "
                               f"spreads only; no naked positions")
        out.active_signals.append(ActiveSignal(
            f"VIX {vix:.1f} — high vol · use defined-risk spreads only", "warn", 3))

    else:
        tag = "PANIC"
        out.verdicts["vix"] = (f"{vix:.1f} — PANIC · "
                               f"long vol only; avoid all short-premium")
        out.active_signals.append(ActiveSignal(
            f"VIX {vix:.1f} PANIC — buy straddle / strangle; no short gamma", "warn", 1))

    return tag


def score_iv_crush(vix: float, fut_signal: str, out: DecisionResult) -> None:
    """
    Fires fast when VIX drops sharply from its recent peak while OI
    suggests positions are still open (buildup) rather than being
    unwound — the pattern that causes Vega losses to eat a
    correctly-directioned Delta gain right after an event.

    History is a RollingWindow (see _VIX_HISTORY above), module-level
    since a fresh DecisionEngine() is created every tick — instance
    state would never persist between polls otherwise.
    """
    if vix <= 0:
        return

    now = time.time()
    _VIX_HISTORY.append(now, vix)  # prunes anything older than
                                    # T.IV_CRUSH_MAX_AGE_SECONDS internally

    # Recent peak = highest VIX reading within the crush window,
    # excluding the current reading itself.
    window_start = now - T.IV_CRUSH_WINDOW_SECONDS
    recent = _VIX_HISTORY.values_since(window_start, before=now)
    if not recent:
        return

    peak = max(recent)
    if peak <= 0:
        return

    pct_drop = (peak - vix) / peak * 100.0
    if pct_drop < T.IV_CRUSH_PCT:
        return

    # "Positions still open" proxy — buildup signals mean traders are
    # still holding/adding, which is exactly when a Vega crush bites
    # hardest. Unwinding/covering means the position's already closing,
    # so the crush is less of a live risk.
    sig = (fut_signal or "").lower()
    still_open = "buildup" in sig

    severity = "warn" if still_open else "info"
    open_note = "positions still building — Vega loss likely outweighing Delta gain" \
                if still_open else "positions already unwinding"
    out.active_signals.append(ActiveSignal(
        f"IV crush: VIX {peak:.1f} → {vix:.1f} ({pct_drop:.1f}% drop within "
        f"{T.IV_CRUSH_WINDOW_SECONDS//60}m) — {open_note}",
        severity, 0 if still_open else 6))


def score_max_pain(spot, max_pain, dist, dte, atm_theta,
                    out: DecisionResult) -> float:
    """
    Spot > max_pain → gravity is BEARISH (pulls down).
    Spot < max_pain → gravity is BULLISH (pulls up).
    Returns score in [-1, +1].
    """
    if max_pain <= 0:
        out.verdicts["maxPain"] = "Not computed"
        return 0.0

    gap  = spot - max_pain          # positive = spot above pain
    direction = "above" if gap > 0 else "below"

    if dist < T.MP_PIN:
        out.verdicts["maxPain"] = (f"₹{max_pain:,.0f} — Spot pinned "
                                   f"(₹{dist:.0f} away) · expiry pin likely")
        out.active_signals.append(ActiveSignal(
            f"Spot within ₹{dist:.0f} of Max Pain ₹{max_pain:,.0f} — pin risk high", "info", 10))
        return 0.0

    elif dist < T.MP_GRAVITY:
        out.verdicts["maxPain"] = (f"₹{max_pain:,.0f} — Spot ₹{dist:.0f} {direction} · "
                                   f"mild mean-reversion pressure")
        raw = -gap / T.MP_GRAVITY   # negative = above pain = mild bearish pull
        return max(-0.40, min(0.40, raw))

    else:
        severity = "warn" if dist > 150 else "info"
        out.verdicts["maxPain"] = (f"₹{max_pain:,.0f} — Spot ₹{dist:.0f} {direction} · "
                                   f"strong gravity toward ₹{max_pain:,.0f}")
        out.active_signals.append(ActiveSignal(
            f"Spot ₹{dist:.0f} {direction} Max Pain — strong reversion before expiry", severity, 8))
        raw = -gap / (dist + 1e-9)
        return max(-1.0, min(1.0, raw))


def score_oi_velocity(vel_df, spot: float, step: int,
                       out: DecisionResult,
                       vol_oi_ratios: dict | None = None) -> float:
    """
    vel_df columns: Strike, CE_OI, CE_OI_Delta, PE_OI, PE_OI_Delta, Signal, IsATM

    Per-strike normalised score:
        CE writing → resistance building → BEARISH (–)
        PE writing → support building   → BULLISH (+)

    Volume confirmation (new):
        Each strike's OI-delta contribution is multiplied by a vol/OI
        conviction factor derived from vol_oi_ratios (from engine.py's
        _build_vol_oi_ratios). Writing +5 000 OI with 50 000 vol (ratio=10)
        is far more meaningful than the same OI move with 500 vol (ratio=0.1).
    """
    if vel_df is None or vel_df.empty:
        return 0.0

    vol_map = vol_oi_ratios or {}
    annotations = {}
    strike_scores: list[float] = []

    for _, row in vel_df.iterrows():
        strike = int(row.get("Strike", 0))
        ce_oi  = float(row.get("CE_OI", 0) or 0)
        pe_oi  = float(row.get("PE_OI", 0) or 0)
        ce_doi = float(row.get("CE_OI_Delta", 0) or 0)
        pe_doi = float(row.get("PE_OI_Delta", 0) or 0)
        is_atm = bool(row.get("IsATM", False))

        ce_pct = ce_doi / ce_oi if ce_oi > 0 else 0.0
        pe_pct = pe_doi / pe_oi if pe_oi > 0 else 0.0

        # ── Volume confirmation look-up (graceful: defaults to 1.0 if absent) ─
        vol_entry  = vol_map.get(str(strike), {})
        ce_vol_oi  = float(vol_entry.get("ce", 0.0))
        pe_vol_oi  = float(vol_entry.get("pe", 0.0))
        ce_vol_mul = _vol_multiplier(ce_vol_oi)
        pe_vol_mul = _vol_multiplier(pe_vol_oi)
        ce_vol_abs = int(vol_entry.get("ce_vol", 0))
        pe_vol_abs = int(vol_entry.get("pe_vol", 0))

        annotations[str(strike)] = {
            "ce": _vel_verdict(ce_doi, ce_pct, "CE"),
            "pe": _vel_verdict(pe_doi, pe_pct, "PE"),
            "ceVolOI": round(ce_vol_oi, 3),
            "peVolOI": round(pe_vol_oi, 3),
        }

        s_score = 0.0
        atm_tag = " [ATM]" if is_atm else ""

        for otype, doi, pct, vol_mul, vol_oi_r, vol_abs in [
            ("CE", ce_doi, ce_pct, ce_vol_mul, ce_vol_oi, ce_vol_abs),
            ("PE", pe_doi, pe_pct, pe_vol_mul, pe_vol_oi, pe_vol_abs),
        ]:
            strength = abs(pct)
            if strength < T.OI_VEL_MILD:
                continue

            # Weight by distance-from-ATM: ATM strikes score full weight
            dist_steps = abs(strike - spot) / step if step > 0 else 1
            proximity_wt = max(0.3, 1.0 - dist_steps * 0.15)

            if   otype == "CE" and doi > 0:    # CE writing → bearish
                contrib = -strength * proximity_wt * vol_mul
                label, direction, sev = "resistance", "building", "warn"
            elif otype == "PE" and doi > 0:    # PE writing → bullish
                contrib = +strength * proximity_wt * vol_mul
                label, direction, sev = "support", "building", "ok"
            elif otype == "CE" and doi < 0:    # CE unwinding → mild bullish
                contrib = +strength * proximity_wt * 0.5 * vol_mul
                label, direction, sev = "resistance", "weakening", "ok"
            else:                               # PE unwinding → mild bearish
                contrib = -strength * proximity_wt * 0.5 * vol_mul
                label, direction, sev = "support", "weakening", "warn"

            s_score += contrib

            if strength >= T.OI_VEL_MODERATE:
                action = "Writing" if doi > 0 else "Unwinding"
                # Enrich signal text with volume context when available
                vol_tag = ""
                if vol_oi_r >= _VOL_CONFIRM_STRONG:
                    vol_tag = f" · vol {vol_abs:,} (high conviction)"
                elif vol_oi_r >= _VOL_CONFIRM_MODERATE:
                    vol_tag = f" · vol {vol_abs:,} (confirmed)"
                elif vol_oi_r > 0 and vol_oi_r < _VOL_CONFIRM_WEAK:
                    vol_tag = f" · low vol {vol_abs:,} (weak conviction)"
                out.active_signals.append(ActiveSignal(
                    f"{otype} {action} at {strike}{atm_tag} ({pct:+.0%})"
                    f" — {label} {direction}{vol_tag}",
                    sev, 15 + len(strike_scores)))

        strike_scores.append(max(-1.0, min(1.0, s_score)))

    out.oi_annotations = annotations
    if not strike_scores:
        return 0.0
    # Average normalised per-strike scores
    return max(-1.0, min(1.0, sum(strike_scores) / len(strike_scores)))


def _vel_verdict(doi: float, pct: float, otype: str) -> str:
    if doi == 0:
        return "Unchanged"
    action   = "Writing" if doi > 0 else "Unwinding"
    strength = ("aggressive" if abs(pct) > T.OI_VEL_STRONG
                else "moderate" if abs(pct) > T.OI_VEL_MODERATE
                else "mild")
    if   otype == "CE" and doi > 0: impl = "resistance building"
    elif otype == "CE" and doi < 0: impl = "resistance weakening"
    elif otype == "PE" and doi > 0: impl = "support building"
    else:                           impl = "support weakening"
    return f"{action} ({pct:+.0%}) · {strength} · {impl}"


def score_walls(ce_wall, pe_wall, spot, atm, step,
                 out: DecisionResult):
    ce_dist   = ce_wall - spot
    pe_dist   = spot - pe_wall
    range_pts = ce_wall - pe_wall

    # Verdicts — guard sign before hardcoding direction label
    if ce_dist >= 0:
        out.verdicts["ceWall"] = f"₹{ce_wall:,.0f} — {ce_dist:.0f}pts above spot"
    else:
        out.verdicts["ceWall"] = f"₹{ce_wall:,.0f} — {abs(ce_dist):.0f}pts BELOW spot (inverted wall)"

    if pe_dist >= 0:
        out.verdicts["peWall"] = f"₹{pe_wall:,.0f} — {pe_dist:.0f}pts below spot"
    else:
        out.verdicts["peWall"] = f"₹{pe_wall:,.0f} — {abs(pe_dist):.0f}pts ABOVE spot (inverted wall)"

    # Proximity signals — only fire when wall is on correct side
    if 0 < ce_dist <= step * 2:
        out.active_signals.append(ActiveSignal(
            f"CE wall ₹{ce_wall:,.0f} only {ce_dist:.0f}pts above — strong resistance cap",
            "warn", 12))

    if 0 < pe_dist <= step * 2:
        out.active_signals.append(ActiveSignal(
            f"PE wall ₹{pe_wall:,.0f} only {pe_dist:.0f}pts below — strong support floor",
            "ok", 12))

    # Iron condor — spot must be between walls, range >= 2 steps
    spot_is_trapped = (ce_wall > spot > pe_wall)
    if spot_is_trapped and 0 < range_pts <= step * 4 and range_pts >= step * 2:
        out.active_signals.append(ActiveSignal(
            f"Spot trapped CE ₹{ce_wall:,.0f} / PE ₹{pe_wall:,.0f} "
            f"({range_pts:.0f}pts) — iron condor zone", "info", 20))


def score_smart_money(smart_money_top, spot: float, atm: float,
                       step: int, out: DecisionResult) -> float:
    """
    Read er.smart_money_top (top-4 CE vol/OI strikes computed in engine.py).

    Logic:
    • If the highest vol/OI strike is a CE strike ABOVE spot → smart money
      is aggressively selling calls → BEARISH confirmation.
    • If it is a PE strike BELOW spot → smart money is selling puts →
      BULLISH confirmation.
    • CE score and PE score both contribute; net = pe_score – ce_score.

    Score range [-1, +1]. Returns 0.0 when smart_money_top is None/empty.
    """
    if smart_money_top is None:
        return 0.0
    try:
        if hasattr(smart_money_top, 'empty') and smart_money_top.empty:
            return 0.0
    except Exception:
        return 0.0

    ce_conviction = 0.0   # bearish contribution
    pe_conviction = 0.0   # bullish contribution
    signals_fired = False

    try:
        for _, row in smart_money_top.iterrows():
            strike    = float(row.get("StrikePrice", 0) or 0)
            ce_score  = float(row.get("CE_Score", 0) or 0)
            pe_score  = float(row.get("PE_Score", 0) or 0)

            # Only count strikes on the correct side of spot
            ce_relevant = ce_score > 1.0 and strike >= atm   # OTM call side
            pe_relevant = pe_score > 1.0 and strike <= atm   # OTM put side

            if ce_relevant:
                ce_conviction += min(ce_score / 20.0, 0.5)  # normalise; cap 0.5
            if pe_relevant:
                pe_conviction += min(pe_score / 20.0, 0.5)

            if (ce_relevant or pe_relevant) and not signals_fired:
                side  = "CE" if ce_relevant else "PE"
                ratio = ce_score if ce_relevant else pe_score
                sev   = "warn" if ce_relevant else "ok"
                out.active_signals.append(ActiveSignal(
                    f"Smart money: {side} vol/OI {ratio:.1f}× at ₹{strike:,.0f} "
                    f"— {'bearish call writing' if ce_relevant else 'bullish put writing'} conviction",
                    sev, 22))
                signals_fired = True

    except Exception:
        return 0.0

    # Net: positive → more PE conviction (bullish), negative → more CE (bearish)
    net = pe_conviction - ce_conviction
    return max(-1.0, min(1.0, net))


# ── Verdicts ──────────────────────────────────────────────────────────────

def verdict_pcr(pcr: float, oi_chg_pcr: float, out: DecisionResult):
    if   pcr >= T.PCR_BULL_EXTREME:
        v = f"{pcr:.2f} — Extreme put writing · very strong bullish signal · shorts covering aggressively"
    elif pcr >= T.PCR_BULL:
        v = f"{pcr:.2f} — Put writing dominant · bullish lean · support expected to hold"
    elif pcr <= T.PCR_BEAR_EXTREME:
        v = f"{pcr:.2f} — Extreme call writing · very strong bearish signal · upside heavily capped"
    elif pcr <= T.PCR_BEAR:
        v = f"{pcr:.2f} — Call writing dominant · bearish lean · resistance building"
    else:
        v = f"{pcr:.2f} — Balanced OI · no clear directional edge"
    out.verdicts["pcr"] = v

    # Intraday drift signal
    if oi_chg_pcr > 0 and abs(oi_chg_pcr - pcr) > 0.25:
        drift = "rising (intraday put writing picking up)" if oi_chg_pcr > pcr \
                else "falling (intraday call writing picking up)"
        out.active_signals.append(ActiveSignal(
            f"OI-chg PCR {oi_chg_pcr:.2f} vs total PCR {pcr:.2f} — sentiment {drift}",
            "info", 25))


def verdict_iv(base_iv: float, iv_rank: float, out: DecisionResult):
    iv_pct = base_iv * 100
    if   iv_rank >= T.IV_EXTREME: regime = "Extreme · premium sellers have a strong structural edge"
    elif iv_rank >= T.IV_HIGH:    regime = "Elevated · credit strategies favoured"
    elif iv_rank >= T.IV_MID:     regime = "Moderate · spreads / mixed directional"
    elif iv_rank >= T.IV_LOW:     regime = "Low-moderate · debit or spreads"
    else:                          regime = "Low · buy options cheaply; avoid selling"
    out.verdicts["atmIV"]  = f"{iv_pct:.1f}% ATM IV"
    out.verdicts["ivRank"] = f"IV Rank {iv_rank:.0f} — {regime}"


def verdict_dte(dte: int, out: DecisionResult):
    if   dte == 0: v = "Expiry day · theta collapses; directional bets only"
    elif dte == 1: v = f"{dte}d — Final session · theta spike; sell premium closing window"
    elif dte <= 3: v = f"{dte}d — Near expiry · theta accelerating; credit plays favoured"
    elif dte <= 7: v = f"{dte}d — This week · credit spreads viable"
    else:          v = f"{dte}d — Time intact · debit spreads / long options viable"
    out.verdicts["dte"] = v


# ===========================================================================
# Trap detector. Was already module-level in decision_engine.py (moved there
# from engine.py per a prior migration step); moved again here as part of
# the same package split. Pure move + un-leak of the name: engine.py was
# reaching into decision_engine.py's private `_detect_traps` across a
# module boundary — now imported as the public `detect_traps`. No signature
# or behavior changes.
# ===========================================================================

def detect_traps(
    spot: float,
    atm: float,
    ce_wall: float,
    pe_wall: float,
    strike_step: int,
    total_pcr: float,
    base_iv: float,
    india_vix: float,
    vel_df: "pd.DataFrame | None",
    bull_trap_iv_spike: float = 0.03,
    bear_trap_pcr_min:  float = T.PCR_BEAR,
    wall_proximity_pts: int   = 2,
) -> dict:
    traps_active: list[str] = []
    warnings:     list[str] = []

    ce_writing_near_wall = False
    pe_writing_near_wall = False
    both_sides_building  = False

    if vel_df is not None and not vel_df.empty:
        for _, row in vel_df.iterrows():
            strike = float(row.get("Strike", 0))
            ce_doi = float(row.get("CE_OI_Delta", 0) or 0)
            pe_doi = float(row.get("PE_OI_Delta", 0) or 0)
            ce_oi  = float(row.get("CE_OI", 1) or 1)
            pe_oi  = float(row.get("PE_OI", 1) or 1)
            if abs(strike - ce_wall) <= strike_step * wall_proximity_pts and ce_doi > 0 and ce_doi / ce_oi > 0.05:
                ce_writing_near_wall = True
            if abs(strike - pe_wall) <= strike_step * wall_proximity_pts and pe_doi > 0 and pe_doi / pe_oi > 0.05:
                pe_writing_near_wall = True
        net_ce = vel_df["CE_OI_Delta"].fillna(0).sum() if "CE_OI_Delta" in vel_df.columns else 0
        net_pe = vel_df["PE_OI_Delta"].fillna(0).sum() if "PE_OI_Delta" in vel_df.columns else 0
        both_sides_building = (net_ce > 0 and net_pe > 0)

    ce_dist_pts  = ce_wall - spot
    pe_dist_pts  = spot - pe_wall
    near_ce_wall = 0 < ce_dist_pts <= strike_step * wall_proximity_pts
    near_pe_wall = 0 < pe_dist_pts <= strike_step * wall_proximity_pts
    tight_channel = (ce_wall - pe_wall) <= strike_step * 4 and ce_wall > spot > pe_wall

    if near_ce_wall and ce_writing_near_wall and base_iv > (DEFAULT_BASE_IV + bull_trap_iv_spike):
        traps_active.append("BULL_TRAP")
        warnings.append(
            f"Bull trap risk — CE wall ₹{ce_wall:,.0f} only {ce_dist_pts:.0f} pts above; "
            f"CE OI building + IV elevated ({base_iv*100:.1f}%). Avoid chasing CE."
        )

    if near_pe_wall and pe_writing_near_wall and total_pcr < bear_trap_pcr_min:
        traps_active.append("BEAR_TRAP")
        warnings.append(
            f"Bear trap risk — PE wall ₹{pe_wall:,.0f} only {pe_dist_pts:.0f} pts below; "
            f"PE writers active + PCR {total_pcr:.2f}. Avoid naked PE shorts."
        )

    if abs(spot - atm) <= strike_step * 1.0:
        traps_active.append("PIN_RISK")
        warnings.append(
            f"Pin risk — spot ₹{spot:,.0f} within {abs(spot-atm):.0f} pts of ATM ₹{atm:,.0f}."
        )

    if tight_channel and both_sides_building:
        traps_active.append("SQUEEZE")
        warnings.append(
            f"OI squeeze — spot trapped CE ₹{ce_wall:,.0f} / PE ₹{pe_wall:,.0f} "
            f"({ce_wall - pe_wall:.0f} pts), both sides building."
        )

    ce_otm_dist = ce_wall - atm
    pe_otm_dist = atm - pe_wall
    atm_skew, skew_warn = 0.0, ""
    if ce_otm_dist > 0 and pe_otm_dist > 0:
        atm_skew = round((pe_otm_dist - ce_otm_dist) / max(pe_otm_dist + ce_otm_dist, 1), 3)
        if atm_skew > 0.15:
            skew_warn = (f"Put skew elevated ({atm_skew:.2f}) — OTM put IV bid heavy.")
        elif atm_skew < -0.15:
            skew_warn = (f"Call skew elevated ({atm_skew:.2f}) — unusual breakout positioning.")

    n_traps = len(traps_active)
    vix_penalty = 2 if india_vix > 24 else (1 if india_vix > T.VIX_NORMAL else 0)
    trade_grade = {0: "A", 1: "B", 2: "C"}.get(n_traps + vix_penalty, "D")

    return {
        "trap_str":    traps_active[0] if traps_active else "BALANCED",
        "trap_warn":   " | ".join(warnings) if warnings else "None",
        "skew_warn":   skew_warn,
        "atm_skew":    atm_skew,
        "trade_grade": trade_grade,
    }
