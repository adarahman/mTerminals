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
"""

from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List, Tuple


# ── Thresholds ────────────────────────────────────────────────────────────────
# Tune per symbol: BANKNIFTY → MP_GRAVITY ~150, OI_VEL_STRONG ~0.20

class T:
    # PCR: PE_OI / CE_OI
    # HIGH PCR (>1.2) → heavy put writing → BULLISH
    # LOW  PCR (<0.8) → heavy call writing → BEARISH
    PCR_BULL_EXTREME  = 1.40   # very strong bullish
    PCR_BULL          = 1.20
    PCR_BEAR          = 0.80
    PCR_BEAR_EXTREME  = 0.60   # very strong bearish
    PCR_NEUTRAL_HI    = 1.10
    PCR_NEUTRAL_LO    = 0.90

    VIX_LOW    = 13.0          # sell-premium regime
    VIX_NORMAL = 18.0
    VIX_HIGH   = 22.0          # reduce short gamma
    VIX_PANIC  = 26.0          # long vol only

    MP_GRAVITY      = 80       # pts; strong pull below this
    MP_PIN          = 30       # pts; pin-zone
    OI_VEL_MILD     = 0.08
    OI_VEL_MODERATE = 0.15
    OI_VEL_STRONG   = 0.25

    IV_LOW     = 30            # iv_rank
    IV_MID     = 50
    IV_HIGH    = 65
    IV_EXTREME = 80

    # Below this confidence (or on WAIT/conflict), a strategy is still
    # computed and shown (so the person can see what the engine WOULD do),
    # but should not be presented as execute-ready.
    CONFIDENCE_EXECUTE_MIN = 40

    # IV crush: a fast VIX drop from its recent peak while positions are
    # still open (buildup, not unwinding) — the classic post-event trap
    # where Vega losses eat a correctly-directioned Delta gain.
    IV_CRUSH_WINDOW_SECONDS = 300   # look back this far for the recent peak
    IV_CRUSH_PCT            = 8.0   # % drop from peak that counts as a crush
    IV_CRUSH_MAX_AGE_SECONDS = 900  # prune history older than this


# DecisionEngine.evaluate() runs on a fresh DecisionEngine() instance every
# tick (see mTerminals_json.export_dashboard_json()), so per-instance state
# never survives between polls. India VIX is a single market-wide reading
# regardless of symbol/expiry, so one process-level history is correct even
# though ws_server_live.py runs one process per --symbol.
_VIX_HISTORY: deque[tuple[float, float]] = deque()


# ── Signal priority order (lower = shown first) ───────────────────────────────
_SEVERITY_ORDER = {"warn": 0, "ok": 1, "info": 2}


# ── Output types ──────────────────────────────────────────────────────────────

@dataclass
class ActiveSignal:
    text:     str
    severity: str = "info"     # "ok" | "info" | "warn"
    priority: int = 99         # lower surfaces first


@dataclass
class DecisionResult:
    # ── Headline block ────────────────────────────────────────────────────────
    bias:           str = "NEUTRAL"   # BULLISH | BEARISH | NEUTRAL — direction always
                                       # from the weighted composite. See conflict_flag
                                       # for sub-signal disagreement (no longer folded
                                       # into bias itself — direction is preserved).
    bias_strength:  str = "WEAK"      # WEAK | MODERATE | STRONG — forced WEAK on conflict
    confidence:     int = 0           # 0–95
    conflict_flag:  bool = False       # True when sub-signals disagree

    # ── Action block ──────────────────────────────────────────────────────────
    action:             str          = ""
    action_type:        str          = "WAIT"
    suggested_strike:   Optional[int]= None

    # ── Strategy block ────────────────────────────────────────────────────────
    suggested_strategy: str  = ""
    auto_strategy:      dict = field(default_factory=dict)
    # Whether the auto_strategy above should be presented as execute-ready.
    # _suggest_strategy() always returns *a* strategy (even under WAIT, it
    # picks the range-appropriate one, e.g. Iron Condor when NEUTRAL) — that
    # part is correct. What was missing is a flag tying the Execute button's
    # state back to the same WAIT/conflict/confidence read shown in the
    # headline block, so the two panels can't visually disagree.
    execute_recommended: bool = True
    strategy_caution:    str  = ""   # human-readable reason(s) when False

    # ── Supporting info ───────────────────────────────────────────────────────
    active_signals:  List[ActiveSignal] = field(default_factory=list)
    verdicts:        dict = field(default_factory=dict)
    oi_annotations:  dict = field(default_factory=dict)

    # ── Score debug (strip in prod if desired) ────────────────────────────────
    _debug: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        sigs = sorted(self.active_signals,
                      key=lambda s: (_SEVERITY_ORDER.get(s.severity, 9), s.priority))
        return {
            "bias":              self.bias,
            "biasStrength":      self.bias_strength,
            "confidence":        self.confidence,
            "conflictFlag":      self.conflict_flag,
            "action":            self.action,
            "actionType":        self.action_type,
            "suggestedStrike":   self.suggested_strike,
            "suggestedStrategy": self.suggested_strategy,
            "executeRecommended": self.execute_recommended,
            "strategyCaution":    self.strategy_caution,
            "activeSignals":     [{"text": s.text, "severity": s.severity}
                                  for s in sigs],
            "verdicts":          self.verdicts,
            "oiAnnotations":     self.oi_annotations,
            "autoStrategy":      self.auto_strategy,
            "_debug":            self._debug,
        }


# ── Main engine ───────────────────────────────────────────────────────────────

class DecisionEngine:
    """
    Call inside mTerminals_json.export_dashboard_json():

        from decision_engine import DecisionEngine
        payload["decision"] = DecisionEngine().evaluate(engine_result, ctx_dict).to_dict()
    """

    def evaluate(self, er, ctx_dict: dict) -> DecisionResult:
        out = DecisionResult()

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
        pcr_score  = self._score_pcr(total_pcr)
        bias_score = self._score_engine_bias(bias_str)
        fut_score  = self._score_futures(fut_signal, basis)
        vix_tag    = self._score_vix(india_vix, out)
        self._score_iv_crush(india_vix, fut_signal, out)
        mp_score   = self._score_max_pain(spot, max_pain, max_pain_dist,
                                          dte, atm_theta, out)
        # OI velocity with volume confirmation multiplier (vol_oi_ratios may be {})
        oi_score   = self._score_oi_velocity(vel_df, spot, strike_step, out,
                                             vol_oi_ratios=vol_oi_ratios)
        # Smart money: top vol/OI strikes as a lightweight confirmation signal
        sm_score   = self._score_smart_money(smart_money_top, spot, atm, strike_step, out)

        self._score_walls(ce_wall, pe_wall, spot, atm, strike_step, out)

        # ── Conflict detection ────────────────────────────────────────────────
        # A conflict exists when directional sub-scores point opposite ways strongly
        directional_scores = [pcr_score, bias_score, fut_score, mp_score, oi_score, sm_score]
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
            pcr_score  * 0.26 +
            bias_score * 0.26 +
            fut_score  * 0.18 +
            mp_score   * 0.12 +
            oi_score   * 0.10 +
            sm_score   * 0.08
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
        }

        # ── Top-line derivation ───────────────────────────────────────────────
        out.bias, out.bias_strength = self._derive_bias(composite, conflict)
        out.confidence = self._compute_confidence(
            composite, conflict, vix_tag, pos, neg, dte, pcr_score, oi_score, sm_score)
        out.action, out.action_type, out.suggested_strike = self._derive_action(
            out.bias, out.bias_strength, atm, strike_step, vix_tag, iv_rank)
        # Optional: real OTM wing LTPs for Iron Condor / PANIC strangle pricing.
        # Not every caller populates this yet — gracefully falls back to None
        # inside _suggest_strategy, which reports netPremium as None (unknown)
        # rather than fabricating a 0.0 "free trade" figure.
        wing_ltp = getattr(er, "wing_premiums", None)

        out.suggested_strategy, out.auto_strategy = self._suggest_strategy(
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
        out.execute_recommended = not caution_reasons
        out.strategy_caution = " · ".join(caution_reasons)

        # ── Verdicts (supporting detail) ──────────────────────────────────────
        self._verdict_pcr(total_pcr, oi_chg_pcr, out)
        self._verdict_iv(base_iv, iv_rank, out)
        self._verdict_dte(dte, out)

        return out

    # ── Sub-scorers ───────────────────────────────────────────────────────────

    def _score_pcr(self, pcr: float) -> float:
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

    def _score_engine_bias(self, bias_str: str) -> float:
        b = bias_str.lower()
        if "strong bull" in b: return +1.0
        if "bull"        in b: return +0.60
        if "strong bear" in b: return -1.0
        if "bear"        in b: return -0.60
        return 0.0

    def _score_futures(self, fut_signal: str, basis: float) -> float:
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

    def _score_vix(self, vix: float, out: DecisionResult) -> str:
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

    def _score_iv_crush(self, vix: float, fut_signal: str, out: DecisionResult) -> None:
        """
        Fires fast when VIX drops sharply from its recent peak while OI
        suggests positions are still open (buildup) rather than being
        unwound — the pattern that causes Vega losses to eat a
        correctly-directioned Delta gain right after an event.

        History is a module-level deque (see _VIX_HISTORY above) since a
        fresh DecisionEngine() is created every tick — instance state
        would never persist between polls otherwise.
        """
        if vix <= 0:
            return

        now = time.time()
        _VIX_HISTORY.append((now, vix))

        # Prune anything older than IV_CRUSH_MAX_AGE_SECONDS so this deque
        # never grows unbounded across a long trading session.
        cutoff = now - T.IV_CRUSH_MAX_AGE_SECONDS
        while _VIX_HISTORY and _VIX_HISTORY[0][0] < cutoff:
            _VIX_HISTORY.popleft()

        # Recent peak = highest VIX reading within the crush window,
        # excluding the current reading itself.
        window_start = now - T.IV_CRUSH_WINDOW_SECONDS
        recent = [v for ts, v in _VIX_HISTORY if ts >= window_start and ts < now]
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

    def _score_max_pain(self, spot, max_pain, dist, dte, atm_theta,
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

    # ── Volume confirmation constants ─────────────────────────────────────────
    # Vol/OI ratio thresholds — classify conviction level behind an OI move.
    # A high ratio means many contracts traded relative to open interest:
    # strong conviction (new money, not just rollover).
    _VOL_CONFIRM_STRONG   = 1.0    # vol/OI ≥ 1.0 → full conviction  (multiplier 1.3)
    _VOL_CONFIRM_MODERATE = 0.30   # vol/OI ≥ 0.3 → confirmed         (multiplier 1.15)
    _VOL_CONFIRM_WEAK     = 0.05   # vol/OI ≥ 0.05 → marginal          (multiplier 1.0)
    # Below _VOL_CONFIRM_WEAK → price-only adjustment, penalise conviction (×0.75)

    def _vol_multiplier(self, vol_oi: float) -> float:
        """Convert a vol/OI ratio into a [0.75 – 1.30] conviction multiplier."""
        if vol_oi >= self._VOL_CONFIRM_STRONG:
            return 1.30
        if vol_oi >= self._VOL_CONFIRM_MODERATE:
            return 1.15
        if vol_oi >= self._VOL_CONFIRM_WEAK:
            return 1.00
        return 0.75   # OI moved but almost no volume → likely roll / adjustment

    def _score_oi_velocity(self, vel_df, spot: float, step: int,
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
            ce_vol_mul = self._vol_multiplier(ce_vol_oi)
            pe_vol_mul = self._vol_multiplier(pe_vol_oi)
            ce_vol_abs = int(vol_entry.get("ce_vol", 0))
            pe_vol_abs = int(vol_entry.get("pe_vol", 0))

            annotations[str(strike)] = {
                "ce": self._vel_verdict(ce_doi, ce_pct, "CE"),
                "pe": self._vel_verdict(pe_doi, pe_pct, "PE"),
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
                    if vol_oi_r >= self._VOL_CONFIRM_STRONG:
                        vol_tag = f" · vol {vol_abs:,} (high conviction)"
                    elif vol_oi_r >= self._VOL_CONFIRM_MODERATE:
                        vol_tag = f" · vol {vol_abs:,} (confirmed)"
                    elif vol_oi_r > 0 and vol_oi_r < self._VOL_CONFIRM_WEAK:
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

    def _vel_verdict(self, doi: float, pct: float, otype: str) -> str:
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

    def _score_walls(self, ce_wall, pe_wall, spot, atm, step,
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

    def _score_smart_money(self, smart_money_top, spot: float, atm: float,
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

    # ── Bias + confidence ─────────────────────────────────────────────────────

    def _derive_bias(self, score: float, conflict: bool) -> Tuple[str, str]:
        """
        Direction always comes from the weighted composite `score` — never
        from the raw pos/neg sub-signal headcount used to set `conflict`.
        Those two checks measure different things (weighted magnitude vs.
        unweighted count) and can legitimately disagree: e.g. PCR + engine
        bias (52% combined weight) can be strongly bullish while 4 minor,
        lightly-weighted signals (fut/maxPain/OI-vel/smart-money, 48%
        combined) each lean just past the 0.15 noise floor in the other
        direction. Discarding the composite's direction in that case would
        mislabel a genuinely bullish read as flatly "CONFLICTED".

        Instead: conflict downgrades conviction (forces WEAK strength, which
        downstream already routes to a WAIT action) while still reporting
        which way the composite leans. `conflict_flag` — set by the caller
        alongside this call — is what actually surfaces the disagreement to
        the UI (⚡ badge), so direction doesn't need to be sacrificed to
        communicate it.
        """
        a = abs(score)
        if a < 0.15:
            direction, strength = "NEUTRAL", "WEAK"
        else:
            strength = "MODERATE" if a < 0.40 else "STRONG"
            direction = "BULLISH" if score > 0 else "BEARISH"

        if conflict:
            strength = "WEAK"

        return direction, strength

    def _compute_confidence(
            self, composite: float, conflict: bool,
            vix_tag: str, pos_count: int, neg_count: int,
            dte: int, pcr_score: float, oi_score: float,
            sm_score: float = 0.0) -> int:
        """
        Base from composite magnitude, then modulate by:
        - Signal confluence (how many sub-scores agree)
        - VIX regime alignment
        - DTE decay
        - Volume-confirmed OI velocity bonus
        - Smart money confirmation bonus
        - Conflict penalty
        """
        base = abs(composite) * 60                    # max 60 from pure direction strength

        # Confluence bonus — each agreeing additional signal adds 5 pts (max +20)
        agree_count = pos_count if composite > 0 else neg_count
        confluence_bonus = min(20, (agree_count - 1) * 5)
        base += confluence_bonus

        # VIX alignment: low VIX + bearish (sell premium edge) or high VIX + bullish
        if vix_tag == "LOW"  and composite < 0:   base += 10
        if vix_tag == "LOW"  and composite > 0:   base +=  5
        if vix_tag in ("VERY_HIGH", "PANIC"):      base -=  8

        # OI velocity confirms (volume-boosted so worth slightly more than before)
        if (oi_score < 0 and composite < 0) or (oi_score > 0 and composite > 0):
            base += 8   # was 7; +1 because oi_score is now volume-confirmed

        # PCR extreme confirms
        if (pcr_score >= 0.65 and composite > 0) or (pcr_score <= -0.65 and composite < 0):
            base += 8

        # Smart money agrees with direction → extra conviction (+5 max)
        if (sm_score > 0.2 and composite > 0) or (sm_score < -0.2 and composite < 0):
            base += 5

        # DTE discount
        if   dte == 0: base *= 0.55
        elif dte == 1: base *= 0.75
        elif dte == 2: base *= 0.88

        # Conflict hard-cap
        if conflict:
            base = min(base, 40)

        return min(95, max(10, int(base)))

    # ── Action derivation ─────────────────────────────────────────────────────

    def _derive_action(self, bias: str, strength: str, atm: float,
                       step: int, vix_tag: str, iv_rank: float):
        atm = int(atm)

        # NOTE: bias is never literally "CONFLICTED" anymore (see _derive_bias) —
        # conflict now downgrades strength to WEAK instead, which the first
        # condition already catches. "CONFLICTED" kept here defensively only.
        if strength == "WEAK" or bias in ("NEUTRAL", "CONFLICTED"):
            return "Wait — insufficient directional edge", "WAIT", None

        if vix_tag == "PANIC":
            if bias == "BEARISH":
                s = atm + step
                return f"Buy {s} PE (long protection, PANIC vol)", "BUY_PE", s
            elif bias == "BULLISH":
                s = atm - step
                return f"Buy {s} CE (long protection, PANIC vol)", "BUY_CE", s
            else:
                return "Long strangle — PANIC regime, direction unclear", "STRANGLE", atm

        if vix_tag in ("HIGH", "VERY_HIGH"):
            # Prefer spreads over naked in high vol
            if bias == "BEARISH" and strength == "STRONG":
                s = atm + step
                return f"Bear Call Spread — sell {s} CE / buy {atm + 2*step} CE", "SPREAD_BEAR", s
            elif bias == "BULLISH" and strength == "STRONG":
                s = atm - step
                return f"Bull Put Spread — sell {s} PE / buy {atm - 2*step} PE", "SPREAD_BULL", s

        # Normal / Low VIX
        if iv_rank >= T.IV_HIGH:
            # Rich premium — prefer selling
            if bias == "BEARISH":
                s = atm + step
                return f"Sell {s} CE (IV rich, bearish)", "SELL_CE", s
            elif bias == "BULLISH":
                s = atm - step
                return f"Sell {s} PE (IV rich, bullish)", "SELL_PE", s
        else:
            # Lean / cheap premium — spreads or debit
            if bias == "BEARISH" and strength == "STRONG":
                s = atm + step
                return f"Bear Call Spread — sell {s} CE / buy {atm + 2*step} CE", "SPREAD_BEAR", s
            elif bias == "BEARISH":
                s = atm + step
                return f"Sell {s} CE", "SELL_CE", s
            elif bias == "BULLISH" and strength == "STRONG":
                s = atm - step
                return f"Bull Put Spread — sell {s} PE / buy {atm - 2*step} PE", "SPREAD_BULL", s
            elif bias == "BULLISH":
                s = atm - step
                return f"Sell {s} PE", "SELL_PE", s

        return "Wait — no clean setup", "WAIT", None

    # ── Strategy builder ──────────────────────────────────────────────────────

    def _suggest_strategy(
            self, bias: str, strength: str, atm: float, step: int,
            ce_ltp: float, pe_ltp: float,
            lot_size: int, expiry: str, dte: int,
            vix_tag: str, iv_rank: float,
            wing_ltp: Optional[dict] = None):
        """
        wing_ltp (optional): {"pe_buy": <ltp at atm-2*step PE>,
                               "ce_buy": <ltp at atm+2*step CE>}
        pulled from the live chain by the caller. ce_ltp/pe_ltp passed into
        this method are ATM-only, so without wing_ltp we have no real price
        for the far OTM legs used by Iron Condor / the PANIC strangle — in
        that case net premium is reported as None rather than a fabricated
        0.0, so downstream consumers don't mistake "unknown" for "zero cost".
        """
        atm = int(atm)
        wing_ltp = wing_ltp or {}
        pe_wing = wing_ltp.get("pe_buy")
        ce_wing = wing_ltp.get("ce_buy")

        def leg(strike, otype, action, ltp=0.0):
            return {"strike": strike, "type": otype, "action": action, "ltp": round(ltp, 2)}

        # ── Strategy selection ────────────────────────────────────────────────
        if vix_tag == "PANIC":
            name = "Long Strangle"
            legs = [
                leg(atm - 2*step, "PE", "BUY",  pe_wing if pe_wing is not None else pe_ltp),
                leg(atm + 2*step, "CE", "BUY",  ce_wing if ce_wing is not None else ce_ltp),
            ]
            # Real wing cost if we have it; otherwise fall back to the ATM
            # premium as a rough (overstated — OTM is always cheaper) proxy.
            net = -((pe_wing if pe_wing is not None else pe_ltp) +
                    (ce_wing if ce_wing is not None else ce_ltp))

        elif bias in ("NEUTRAL", "CONFLICTED") or (
                strength != "STRONG" and iv_rank >= T.IV_HIGH):
            if iv_rank >= T.IV_EXTREME:
                name = "Short Straddle"
                legs = [
                    leg(atm, "CE", "SELL", ce_ltp),
                    leg(atm, "PE", "SELL", pe_ltp),
                ]
                net = ce_ltp + pe_ltp
            else:
                name = "Iron Condor"
                legs = [
                    leg(atm - 2*step, "PE", "BUY",  pe_wing or 0.0),
                    leg(atm -   step, "PE", "SELL", pe_ltp),
                    leg(atm +   step, "CE", "SELL", ce_ltp),
                    leg(atm + 2*step, "CE", "BUY",  ce_wing or 0.0),
                ]
                # Net credit = inner (sold) legs − outer (bought) legs.
                # Only computable when the caller supplied real wing LTPs;
                # otherwise report None instead of a fabricated 0.0 so the
                # UI can show "—" rather than implying a free trade.
                if pe_wing is not None and ce_wing is not None:
                    net = (ce_ltp + pe_ltp) - (ce_wing + pe_wing)
                else:
                    net = None

        elif bias == "BEARISH" and strength == "STRONG":
            name = "Bear Call Spread"
            legs = [
                leg(atm +   step, "CE", "SELL", ce_ltp),
                leg(atm + 2*step, "CE", "BUY",  ce_wing if ce_wing is not None else 0.0),
            ]
            net = ce_ltp   # net credit ≈ short leg (long leg OTM cost deducted caller-side)

        elif bias == "BEARISH":
            if iv_rank >= T.IV_MID:
                name = "Short Strangle"
                legs = [
                    leg(atm + step, "CE", "SELL", ce_ltp),
                    leg(atm - step, "PE", "SELL", pe_ltp),
                ]
                net = ce_ltp + pe_ltp
            else:
                name = "Bear Call Spread"
                legs = [
                    leg(atm +   step, "CE", "SELL", ce_ltp),
                    leg(atm + 2*step, "CE", "BUY",  ce_wing if ce_wing is not None else 0.0),
                ]
                net = ce_ltp

        elif bias == "BULLISH" and strength == "STRONG":
            name = "Bull Put Spread"
            legs = [
                leg(atm -   step, "PE", "SELL", pe_ltp),
                leg(atm - 2*step, "PE", "BUY",  pe_wing if pe_wing is not None else 0.0),
            ]
            net = pe_ltp

        else:   # BULLISH MODERATE
            if iv_rank >= T.IV_MID:
                name = "Short Strangle"
                legs = [
                    leg(atm - step, "PE", "SELL", pe_ltp),
                    leg(atm + step, "CE", "SELL", ce_ltp),
                ]
                net = ce_ltp + pe_ltp
            else:
                name = "Bull Put Spread"
                legs = [
                    leg(atm -   step, "PE", "SELL", pe_ltp),
                    leg(atm - 2*step, "PE", "BUY",  pe_wing if pe_wing is not None else 0.0),
                ]
                net = pe_ltp

        net = round(net, 2) if net is not None else None
        max_profit = round(net * lot_size, 2) if (net is not None and net > 0) else None
        max_loss   = (round((step - net) * lot_size, 2)
                      if (net is not None and name in ("Bear Call Spread", "Bull Put Spread")) else None)

        return name, {
            "name":       name,
            "legs":       legs,
            "netPremium": net,
            "maxProfit":  max_profit,
            "maxLoss":    max_loss,
            "atm":        atm,
            "expiry":     expiry,
            "dte":        dte,
            "lotSize":    lot_size,
            "ivRegime":   vix_tag,
            "ivRank":     round(iv_rank, 1),
        }

    # ── Verdicts ──────────────────────────────────────────────────────────────

    def _verdict_pcr(self, pcr: float, oi_chg_pcr: float, out: DecisionResult):
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

    def _verdict_iv(self, base_iv: float, iv_rank: float, out: DecisionResult):
        iv_pct = base_iv * 100
        if   iv_rank >= T.IV_EXTREME: regime = "Extreme · premium sellers have a strong structural edge"
        elif iv_rank >= T.IV_HIGH:    regime = "Elevated · credit strategies favoured"
        elif iv_rank >= T.IV_MID:     regime = "Moderate · spreads / mixed directional"
        elif iv_rank >= T.IV_LOW:     regime = "Low-moderate · debit or spreads"
        else:                          regime = "Low · buy options cheaply; avoid selling"
        out.verdicts["atmIV"]  = f"{iv_pct:.1f}% ATM IV"
        out.verdicts["ivRank"] = f"IV Rank {iv_rank:.0f} — {regime}"

    def _verdict_dte(self, dte: int, out: DecisionResult):
        if   dte == 0: v = "Expiry day · theta collapses; directional bets only"
        elif dte == 1: v = f"{dte}d — Final session · theta spike; sell premium closing window"
        elif dte <= 3: v = f"{dte}d — Near expiry · theta accelerating; credit plays favoured"
        elif dte <= 7: v = f"{dte}d — This week · credit spreads viable"
        else:          v = f"{dte}d — Time intact · debit spreads / long options viable"
        out.verdicts["dte"] = v