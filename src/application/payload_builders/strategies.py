"""Dashboard strategy-card payload builder."""
from __future__ import annotations

import re

from .common import integer as _to_int, rounded_number as _r, safe_string as _safe_str


def build_strategies(ctx_dict, engine_result=None, chain_rows=None):
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
