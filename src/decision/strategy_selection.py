"""Action derivation + strategy-card builder.

Split out of decision_engine.py's DecisionEngine class. Pure move +
de-methodize — neither function used instance state, only their own
arguments. No logic changes.

Note: despite the name, this is strategy *selection* (which spread/
straddle fits the current bias+regime), not order execution — nothing
here places or manages an actual order. See brokers/market_data.py's
docstring for the equivalent finding on the broker side: an
execution.py would currently have nothing real to contain, since
SmartAPI order placement isn't wired up anywhere yet (PaperTradingEngine
is the only live order path, and it's a separate system this package
doesn't touch).
"""

from __future__ import annotations
from typing import Optional

from decision.types import T


def derive_action(bias: str, strength: str, atm: float,
                   step: int, vix_tag: str, iv_rank: float):
    atm = int(atm)

    # NOTE: bias is never literally "CONFLICTED" anymore (see derive_bias) —
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


def suggest_strategy(
        bias: str, strength: str, atm: float, step: int,
        ce_ltp: float, pe_ltp: float,
        lot_size: int, expiry: str, dte: int,
        vix_tag: str, iv_rank: float,
        wing_ltp: Optional[dict] = None):
    """
    wing_ltp (optional): {"pe_buy": <ltp at atm-2*step PE>,
                           "ce_buy": <ltp at atm+2*step CE>}
    pulled from the live chain by the caller. ce_ltp/pe_ltp passed into
    this function are ATM-only, so without wing_ltp we have no real price
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
