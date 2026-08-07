"""
decision_engine.py  (v2)
────────────────────────
Converts EngineResult → DecisionResult for JSON export via mTerminals_json.py.

Key improvements over v1
────────────────────────
• Conviction-weighted confidence (signal count × quality, not flat integers)
• Conflict detection — opposing sub-signals set conflict_flag and force WEAK
  conviction (→ WAIT action), but bias keeps reporting the composite's actual
  direction (BULLISH/BEARISH/NEUTRAL) instead of being blanked out to a
  directionless "CONFLICTED" tier. The weighted composite and the raw
  pos/neg sub-signal headcount measure different things and can disagree;
  direction always comes from the former.
• PCR nomenclature fixed (LOW PCR = bearish; HIGH PCR = bullish)
• Active signals deduplicated, priority-ranked, severity-gated
• Strategy net premium computed from actual leg LTPs where available
• OI velocity score normalised per-strike before summing
• IV-regime gates on sell/buy recommendations
• Confidence suppressed (capped at 40) when bias is CONFLICTED
• action_type vocabulary expanded: SELL_CE, SELL_PE, BUY_CE, BUY_PE,
  SPREAD_BEAR, SPREAD_BULL, STRADDLE, STRANGLE, CONDOR, WAIT

Scoring, verdicts, trap detection, bias/confidence, and action/strategy
selection now live in sibling modules (signal_builder.py, confidence.py,
strategy_selection.py) — this file only orchestrates the sequence and
owns EngineResult unpacking. T / ActiveSignal / DecisionResult live in
decision/types.py so all four modules can share them without importing
this one.
"""

from __future__ import annotations

from decision.types import T, ActiveSignal, DecisionResult
from decision.signal_builder import (
    score_pcr, score_engine_bias, score_futures, score_vix, score_iv_crush,
    score_max_pain, score_oi_velocity, score_walls, score_smart_money,
    verdict_pcr, verdict_iv, verdict_dte,
)
from decision.confidence import derive_bias, compute_confidence
from decision.strategy_selection import derive_action, suggest_strategy


# ── Main engine ───────────────────────────────────────────────────────────────

class DecisionEngine:
    """
    Call inside mTerminals_json.export_dashboard_json():

        from decision.decision_engine import DecisionEngine
        payload["decision"] = DecisionEngine().evaluate(engine_result, ctx_dict).to_dict()
    """

    def evaluate(self, er, ctx_dict: dict) -> DecisionResult:
        out = DecisionResult()
        out.decision_timestamp = str(ctx_dict.get("_decision_timestamp", ""))
        out.state_version = str(ctx_dict.get("_state_version", ""))

        # ── Unpack EngineResult (exact attribute names) ───────────────────────
        spot          = float(er.spot)
        atm           = float(er.atm)
        strike_step   = int(er.strike_step)
        lot_size      = int(er.lot_size)
        dte           = int(er.dte)
        expiry        = str(er.expiry)
        total_pcr     = float(er.total_pcr)
        oi_chg_pcr    = float(er.oi_chg_pcr)
        max_pain      = float(er.max_pain)
        max_pain_dist = float(er.max_pain_dist)
        ce_wall       = float(er.ce_wall)
        pe_wall       = float(er.pe_wall)
        india_vix     = float(er.india_vix)
        base_iv       = float(er.base_iv)
        iv_rank       = float(er.iv_rank)
        basis         = float(er.basis)
        bias_str      = str(er.bias)        # engine combined_view
        fut_signal    = str(er.fut_signal)  # "Long Buildup" | "Short Buildup" etc.
        ce_premium    = float(er.ce_premium)
        pe_premium    = float(er.pe_premium)
        atm_theta     = float(er.atm_theta)
        vel_df        = er.vel_df           # DataFrame | None
        # Volume confirmation: per-strike vol/OI dict (graceful — may be empty)
        vol_oi_ratios  = getattr(er, "vol_oi_ratios", {}) or {}
        # Smart money top strikes (DataFrame | None)
        smart_money_top = getattr(er, "smart_money_top", None)

        # ── Sub-scores  (all in [-1, +1]; positive = bullish) ─────────────────
        pcr_score  = score_pcr(total_pcr)
        bias_score = score_engine_bias(bias_str)
        fut_score  = score_futures(fut_signal, basis)
        vix_tag    = score_vix(india_vix, out)
        score_iv_crush(india_vix, fut_signal, out)
        mp_score   = score_max_pain(spot, max_pain, max_pain_dist,
                                     dte, atm_theta, out)
        # OI velocity with volume confirmation multiplier (vol_oi_ratios may be {})
        oi_score   = score_oi_velocity(vel_df, spot, strike_step, out,
                                        vol_oi_ratios=vol_oi_ratios)
        # Smart money: top vol/OI strikes as a lightweight confirmation signal
        sm_score   = score_smart_money(smart_money_top, spot, atm, strike_step, out)

        score_walls(ce_wall, pe_wall, spot, atm, strike_step, out)

        # ── Conflict detection ────────────────────────────────────────────────
        # A conflict exists when directional sub-scores point opposite ways strongly
        weights = {
            "pcr": 0.26, "engine_bias": 0.26, "futures": 0.18,
            "max_pain": 0.12, "oi_velocity": 0.10, "smart_money": 0.08,
        }
        availability = {
            "pcr": total_pcr > 0,
            "engine_bias": bool(bias_str.strip()),
            "futures": bool(fut_signal.strip()) and fut_signal.strip().lower() not in ("none", "unknown", "—"),
            "max_pain": spot > 0 and max_pain > 0,
            "oi_velocity": vel_df is not None and not getattr(vel_df, "empty", False),
            "smart_money": smart_money_top is not None and not getattr(smart_money_top, "empty", False),
        }
        score_map = {
            "pcr": pcr_score, "engine_bias": bias_score, "futures": fut_score,
            "max_pain": mp_score, "oi_velocity": oi_score, "smart_money": sm_score,
        }
        labels = {
            "pcr": "PCR positioning", "engine_bias": "Combined market bias",
            "futures": "Futures positioning", "max_pain": "Max-pain gravity",
            "oi_velocity": "OI velocity", "smart_money": "Volume / OI confirmation",
        }
        required = {"pcr", "engine_bias", "futures", "max_pain"}
        out.missing_inputs = [key for key, available in availability.items() if not available]
        critical_missing = any(key in required for key in out.missing_inputs)
        out.degraded = critical_missing
        out.evidence_coverage = round(sum(
            weight for key, weight in weights.items() if availability[key]
        ) * 100)
        out.contributors = [{
            "key": key,
            "label": labels[key],
            "available": availability[key],
            "weight": round(weight * 100),
            "score": round(score_map[key], 3) if availability[key] else None,
            "weightedContribution": round(score_map[key] * weight, 3) if availability[key] else None,
        } for key, weight in weights.items()]

        directional_scores = [score_map[key] for key in weights if availability[key]]
        pos = sum(1 for s in directional_scores if s > 0.15)
        neg = sum(1 for s in directional_scores if s < -0.15)
        conflict = pos >= 2 and neg >= 2
        if conflict:
            out.conflict_flag = True
            out.active_signals.append(ActiveSignal(
                "⚠ Sub-signals are split — reduce size or wait for alignment", "warn", 0))

        # ── Composite score (conviction-weighted) ─────────────────────────────
        # Weights must sum to 1.0.
        # PCR + bias remain the twin anchors (0.26 each after making room for vol/sm).
        # OI velocity gains weight now that it's volume-confirmed (0.14).
        # Smart money is a small confirmation nudge (0.08).
        composite = (
            sum(score_map[key] * weight for key, weight in weights.items()
                if availability[key])
        )
        composite = max(-1.0, min(1.0, composite))

        out._debug = {
            "pcr_score":  round(pcr_score,  3),
            "bias_score": round(bias_score, 3),
            "fut_score":  round(fut_score,  3),
            "mp_score":   round(mp_score,   3),
            "oi_score":   round(oi_score,   3),
            "sm_score":   round(sm_score,   3),
            "vol_oi_available": bool(vol_oi_ratios),
            "composite":  round(composite,  3),
            "conflict":   conflict,
            "vix_tag":    vix_tag,
            "evidence_coverage": out.evidence_coverage,
            "missing_inputs": out.missing_inputs,
        }

        # ── Top-line derivation ───────────────────────────────────────────────
        out.bias, out.bias_strength = derive_bias(composite, conflict)
        out.confidence = compute_confidence(
            composite, conflict, vix_tag, pos, neg, dte, pcr_score, oi_score, sm_score,
            evidence_coverage=out.evidence_coverage / 100.0,
            critical_inputs_missing=critical_missing)
        out.action, out.action_type, out.suggested_strike = derive_action(
            out.bias, out.bias_strength, atm, strike_step, vix_tag, iv_rank)
        # Optional: real OTM wing LTPs for Iron Condor / PANIC strangle pricing.
        # Not every caller populates this yet — gracefully falls back to None
        # inside suggest_strategy, which reports netPremium as None (unknown)
        # rather than fabricating a 0.0 "free trade" figure.
        wing_ltp = getattr(er, "wing_premiums", None)

        out.suggested_strategy, out.auto_strategy = suggest_strategy(
            out.bias, out.bias_strength, atm, strike_step,
            ce_premium, pe_premium, lot_size, expiry, dte, vix_tag, iv_rank,
            wing_ltp=wing_ltp)

        # ── Reconcile the strategy card against the same WAIT/conflict/
        # confidence read the headline block already computed above. Without
        # this, the Strategy panel's Execute button carries no signal that
        # the Decision Engine box next to it says "Wait — insufficient
        # directional edge" or flagged split sub-signals.
        caution_reasons = []
        if out.action_type == "WAIT":
            caution_reasons.append("Decision engine verdict is WAIT — no directional edge")
        if out.conflict_flag:
            caution_reasons.append("Sub-signals are split")
        if out.confidence < T.CONFIDENCE_EXECUTE_MIN:
            caution_reasons.append(
                f"Confidence {out.confidence}% below {T.CONFIDENCE_EXECUTE_MIN}% execution threshold")
        if out.degraded:
            caution_reasons.append("Required decision evidence is missing")
        out.execute_recommended = not caution_reasons
        out.strategy_caution = " · ".join(caution_reasons)

        # ── Verdicts (supporting detail) ──────────────────────────────────────
        verdict_pcr(total_pcr, oi_chg_pcr, out)
        verdict_iv(base_iv, iv_rank, out)
        verdict_dte(dte, out)

        out.trade_grade = str(getattr(er, "trade_grade", "") or "")
        out.risk_warning = str(getattr(er, "trap_warn", "") or "")
        out.important_levels = {
            "atm": int(round(atm)), "maxPain": max_pain,
            "ceWall": ce_wall, "peWall": pe_wall,
        }

        return out
