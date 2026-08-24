"""
strategy/strategies.py
-----------------------
Strategy construction, rule-based scoring/reconciliation, and scenario
P&L simulation, computed once per EngineResult pass: the 15 supported
option strategies (BCS, IC, BPS, SS, CAL, RPS, CC, BFLY, BUPS, BECS, LS,
LSG, LC, LP, PP), their raw scores reconciled against the trap detector
and OI-buildup bias, and the +/-1%/3%/5% spot-shift P&L scenarios.

Moved from engine.py (Step 4b of the v4 migration plan). Pure move +
rename only: no behavioral changes, no signature changes.

Note: mTerminals_json.py has its own, unrelated `_build_strategies`
function (different signature: ctx_dict/engine_result/chain_rows) that
does not import from here and is not affected by this move.
"""

from __future__ import annotations

from decision.types import T
from oi.pricing import (
    ANNUAL_RISK_FREE_RATE,
    _MIN_T_YEARS,
    bs_call,
    bs_put,
    get_iv_skew,
)

__all__ = [
    "_build_strategies",
    "_score_strategies",
    "_build_scenario_pnl",
]


def _fmt_k(value):
    """Compact strategy payoff values without depending on a presenter."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "--"
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if magnitude >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def _build_strategies(spot: float, atm: float, step: int, dte: int,
                        base_iv: float, lot_size: int,
                        near_expiry: str = "", far_expiry: str = "") -> list[dict]:
    t_param = max(dte / 365.0, _MIN_T_YEARS)
    r_param = ANNUAL_RISK_FREE_RATE

    atm_c  = bs_call(spot, atm,          t_param, r_param, base_iv)
    atm_p  = bs_put( spot, atm,          t_param, r_param, base_iv)
    otm_c  = bs_call(spot, atm + step*3, t_param, r_param, get_iv_skew(atm + step*3, spot, base_iv))
    otm_p  = bs_put( spot, atm - step*3, t_param, r_param, get_iv_skew(atm - step*3, spot, base_iv))
    wing_c = bs_call(spot, atm + step*7, t_param, r_param, get_iv_skew(atm + step*7, spot, base_iv))
    wing_p = bs_put( spot, atm - step*7, t_param, r_param, get_iv_skew(atm - step*7, spot, base_iv))
    far_c  = bs_call(spot, atm, t_param * 2, r_param, base_iv * 1.02)

    # fmt_k imported from ui_theme via the module-level import below
    strats = {}

    net_cost_bcs = atm_c - otm_c
    strats['BCS'] = {
        'name': "Bull Call Spread", 'type_code': "BCS", 'risk_level': "Low",
        'color_key': 'CLR_INFO',
        'tags': ["[Bullish]", "[Debit]", "[Defined Risk]"],
        'desc': "Buy ATM call, sell OTM call. Best when IV is low and outlook is moderately bullish.",
        'legs': [f"Buy {atm:,.0f} CE @ \u20b9{atm_c:.1f}",
                 f"Sell {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}", "", ""],
        'max_profit': _fmt_k((step*3 - net_cost_bcs) * lot_size),
        'max_loss':   _fmt_k(net_cost_bcs * lot_size),
        'breakeven':  f"{atm + net_cost_bcs:,.0f}",
        'rr':         f"{(step*3 - net_cost_bcs) / max(net_cost_bcs, 1.0):.1f}:1",
    }

    ic_prem = otm_c + otm_p - wing_c - wing_p
    strats['IC'] = {
        'name': "Iron Condor", 'type_code': "IC", 'risk_level': "Moderate",
        'color_key': 'CLR_SUCCESS',
        'tags': ["[Neutral]", "[Credit]", "[Range-bound]"],
        'desc': "Sell OTM strangle, buy farther OTM wings. Ideal for IV Rank >60.",
        'legs': [f"Sell {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}",
                 f"Buy {atm+step*7:,.0f} CE @ \u20b9{wing_c:.1f}",
                 f"Sell {atm-step*3:,.0f} PE @ \u20b9{otm_p:.1f}",
                 f"Buy {atm-step*7:,.0f} PE @ \u20b9{wing_p:.1f}"],
        'max_profit': _fmt_k(ic_prem * lot_size),
        'max_loss':   _fmt_k((step*4 - ic_prem) * lot_size),
        'breakeven':  f"{atm-step*3-ic_prem:,.0f} / {atm+step*3+ic_prem:,.0f}",
        'rr':         f"{ic_prem / max(step*4 - ic_prem, 1.0):.1f}:1",
    }

    net_cost_bps = atm_p - otm_p
    strats['BPS'] = {
        'name': "Bear Put Spread", 'type_code': "BPS", 'risk_level': "Low",
        'color_key': 'CLR_DN',
        'tags': ["[Bearish]", "[Debit]", "[Defined Risk]"],
        'desc': "Buy ATM put, sell OTM put. Effective when max pain < CMP and PCR rising.",
        'legs': [f"Buy {atm:,.0f} PE @ \u20b9{atm_p:.1f}",
                 f"Sell {atm-step*3:,.0f} PE @ \u20b9{otm_p:.1f}", "", ""],
        'max_profit': _fmt_k((step*3 - net_cost_bps) * lot_size),
        'max_loss':   _fmt_k(net_cost_bps * lot_size),
        'breakeven':  f"{atm - net_cost_bps:,.0f}",
        'rr':         f"{(step*3 - net_cost_bps) / max(net_cost_bps, 1.0):.1f}:1",
    }

    ss_prem = atm_c + atm_p
    strats['SS'] = {
        'name': "Short Straddle", 'type_code': "SS", 'risk_level': "High",
        'color_key': 'CLR_WARN',
        'tags': ["[Neutral]", "[Credit]", "[High IV]"],
        'desc': "Sell ATM call + put. Aggressive theta capture when IV Rank >70. Unlimited risk.",
        'legs': [f"Sell {atm:,.0f} CE @ \u20b9{atm_c:.1f}",
                 f"Sell {atm:,.0f} PE @ \u20b9{atm_p:.1f}", "", ""],
        'max_profit': _fmt_k(ss_prem * lot_size),
        'max_loss':   "Unlimited",
        'breakeven':  f"{atm-ss_prem:,.0f} / {atm+ss_prem:,.0f}",
        'rr':         "N/A",
    }

    cal_cost = far_c - atm_c
    # Use actual expiry date strings when available; fall back to descriptive labels
    _near_lbl = near_expiry if near_expiry else "NEAR"
    _far_lbl  = far_expiry  if far_expiry  else "FAR"
    strats['CAL'] = {
        'name': "Calendar Spread", 'type_code': "CAL", 'risk_level': "Low",
        'color_key': 'CLR_INFO',
        'tags': ["[Neutral]", "[Debit]", "[Theta Change]"],
        'desc': "Buy far-month ATM call, sell near-month. Profits from horizontal decay-skew differentials.",
        'legs': [
            {'action': 'SELL', 'type': 'CE', 'strike': int(atm), 'ltp': round(atm_c, 1), 'expiry': _near_lbl, 'lots': 1},
            {'action': 'BUY',  'type': 'CE', 'strike': int(atm), 'ltp': round(far_c, 1), 'expiry': _far_lbl,  'lots': 1},
        ],
        'max_profit': _fmt_k(cal_cost * lot_size * 2),
        'max_loss':   _fmt_k(cal_cost * lot_size),
        'breakeven':  f"{atm-step*2:,.0f} / {atm+step*2:,.0f}",
        'rr':         "2.0:1",
    }

    rps_prem = atm_p - 2 * otm_p
    strats['RPS'] = {
        'name': "Ratio Put Spread", 'type_code': "RPS", 'risk_level': "High",
        'color_key': 'CLR_WARN',
        'tags': ["[Bearish]", "[Credit/Debit]" if rps_prem >= 0 else "[Net Debit]", "[Complex]"],
        'desc': "Buy 1 ATM put, sell 2 OTM puts. Profits in moderate fall; watch large down-tail risk.",
        'legs': [f"Buy 1x {atm:,.0f} PE @ \u20b9{atm_p:.1f}",
                 f"Sell 2x {atm-step*3:,.0f} PE @ \u20b9{otm_p:.1f}", "", ""],
        'max_profit': _fmt_k((step*3 + rps_prem) * lot_size),
        'max_loss':   f"Unlimited below {atm-step*6:,.0f}",
        'breakeven':  f"{atm - rps_prem:,.0f} (Upper)",
        'rr':         "Varies",
    }

    # ── Covered Call ─────────────────────────────────────────────────────
    strats['CC'] = {
        'name': "Covered Call", 'type_code': "CC", 'risk_level': "Low",
        'color_key': 'CLR_SUCCESS',
        'tags': ["[Neutral]", "[Income]", "[Requires Underlying]"],
        'desc': "Hold the underlying, sell an OTM call against it. Caps upside for steady income.",
        'legs': [f"Buy Underlying @ \u20b9{spot:,.1f}",
                 f"Sell {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}", "", ""],
        'max_profit': _fmt_k((atm+step*3 - spot + otm_c) * lot_size),
        'max_loss':   f"Substantial below \u20b9{spot-otm_c:,.0f}",
        'breakeven':  f"{spot-otm_c:,.0f}",
        'rr':         "Varies",
    }

    # ── Butterfly Spread (long call butterfly) ──────────────────────────
    itm_c = bs_call(spot, atm - step*3, t_param, r_param, get_iv_skew(atm - step*3, spot, base_iv))
    bfly_cost = itm_c + otm_c - 2*atm_c
    strats['BFLY'] = {
        'name': "Butterfly Spread", 'type_code': "BFLY", 'risk_level': "Low",
        'color_key': 'CLR_INFO',
        'tags': ["[Neutral]", "[Debit]", "[Pin Risk]"],
        'desc': "Buy 1 ITM call, sell 2 ATM calls, buy 1 OTM call. Best when spot pins near the body strike.",
        'legs': [f"Buy 1x {atm-step*3:,.0f} CE @ \u20b9{itm_c:.1f}",
                 f"Sell 2x {atm:,.0f} CE @ \u20b9{atm_c:.1f}",
                 f"Buy 1x {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}", ""],
        'max_profit': _fmt_k((step*3 - bfly_cost) * lot_size),
        'max_loss':   _fmt_k(bfly_cost * lot_size),
        'breakeven':  f"{atm-step*3+bfly_cost:,.0f} / {atm+step*3-bfly_cost:,.0f}",
        'rr':         f"{(step*3 - bfly_cost) / max(bfly_cost, 1.0):.1f}:1",
    }

    # ── Bull Put Spread (credit) ─────────────────────────────────────────
    bups_credit = otm_p - wing_p
    strats['BUPS'] = {
        'name': "Bull Put Spread", 'type_code': "BUPS", 'risk_level': "Low",
        'color_key': 'CLR_SUCCESS',
        'tags': ["[Bullish]", "[Credit]", "[Defined Risk]"],
        'desc': "Sell higher-strike put, buy lower-strike put for protection. Collects premium if spot holds above the short strike.",
        'legs': [f"Sell {atm-step*3:,.0f} PE @ \u20b9{otm_p:.1f}",
                 f"Buy {atm-step*7:,.0f} PE @ \u20b9{wing_p:.1f}", "", ""],
        'max_profit': _fmt_k(bups_credit * lot_size),
        'max_loss':   _fmt_k((step*4 - bups_credit) * lot_size),
        'breakeven':  f"{atm-step*3-bups_credit:,.0f}",
        'rr':         f"{bups_credit / max(step*4 - bups_credit, 1.0):.1f}:1",
    }

    # ── Bear Call Spread (credit) ─────────────────────────────────────────
    becs_credit = otm_c - wing_c
    strats['BECS'] = {
        'name': "Bear Call Spread", 'type_code': "BECS", 'risk_level': "Low",
        'color_key': 'CLR_DN',
        'tags': ["[Bearish]", "[Credit]", "[Defined Risk]"],
        'desc': "Sell lower-strike call, buy higher-strike call for protection. Collects premium if spot stays below the short strike.",
        'legs': [f"Sell {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}",
                 f"Buy {atm+step*7:,.0f} CE @ \u20b9{wing_c:.1f}", "", ""],
        'max_profit': _fmt_k(becs_credit * lot_size),
        'max_loss':   _fmt_k((step*4 - becs_credit) * lot_size),
        'breakeven':  f"{atm+step*3+becs_credit:,.0f}",
        'rr':         f"{becs_credit / max(step*4 - becs_credit, 1.0):.1f}:1",
    }

    # ── Long Straddle ──────────────────────────────────────────────────
    ls_cost = atm_c + atm_p
    strats['LS'] = {
        'name': "Long Straddle", 'type_code': "LS", 'risk_level': "Moderate",
        'color_key': 'CLR_INFO',
        'tags': ["[Neutral]", "[Debit]", "[Low IV]"],
        'desc': "Buy ATM call + ATM put. Profits from a big move either way; best entered when IV Rank is low.",
        'legs': [f"Buy {atm:,.0f} CE @ \u20b9{atm_c:.1f}",
                 f"Buy {atm:,.0f} PE @ \u20b9{atm_p:.1f}", "", ""],
        'max_profit': "Unlimited",
        'max_loss':   _fmt_k(ls_cost * lot_size),
        'breakeven':  f"{atm-ls_cost:,.0f} / {atm+ls_cost:,.0f}",
        'rr':         "N/A",
    }

    # ── Long Strangle ──────────────────────────────────────────────────
    lsg_cost = otm_c + otm_p
    strats['LSG'] = {
        'name': "Long Strangle", 'type_code': "LSG", 'risk_level': "Moderate",
        'color_key': 'CLR_INFO',
        'tags': ["[Neutral]", "[Debit]", "[Low IV]"],
        'desc': "Buy OTM call + OTM put. Cheaper than a straddle; needs a bigger move to profit.",
        'legs': [f"Buy {atm+step*3:,.0f} CE @ \u20b9{otm_c:.1f}",
                 f"Buy {atm-step*3:,.0f} PE @ \u20b9{otm_p:.1f}", "", ""],
        'max_profit': "Unlimited",
        'max_loss':   _fmt_k(lsg_cost * lot_size),
        'breakeven':  f"{atm-step*3-lsg_cost:,.0f} / {atm+step*3+lsg_cost:,.0f}",
        'rr':         "N/A",
    }

    # ── Long Call ─────────────────────────────────────────────────────
    strats['LC'] = {
        'name': "Long Call", 'type_code': "LC", 'risk_level': "Moderate",
        'color_key': 'CLR_SUCCESS',
        'tags': ["[Bullish]", "[Debit]", "[Unlimited Upside]"],
        'desc': "Buy a single ATM call. Pure directional bet on a strong upward move.",
        'legs': [f"Buy {atm:,.0f} CE @ \u20b9{atm_c:.1f}", "", "", ""],
        'max_profit': "Unlimited",
        'max_loss':   _fmt_k(atm_c * lot_size),
        'breakeven':  f"{atm+atm_c:,.0f}",
        'rr':         "N/A",
    }

    # ── Long Put ──────────────────────────────────────────────────────
    strats['LP'] = {
        'name': "Long Put", 'type_code': "LP", 'risk_level': "Moderate",
        'color_key': 'CLR_DN',
        'tags': ["[Bearish]", "[Debit]", "[Defined Risk]"],
        'desc': "Buy a single ATM put. Pure directional bet on a strong downward move.",
        'legs': [f"Buy {atm:,.0f} PE @ \u20b9{atm_p:.1f}", "", "", ""],
        'max_profit': _fmt_k(max(atm - atm_p, 0) * lot_size) + " (if spot\u21920)",
        'max_loss':   _fmt_k(atm_p * lot_size),
        'breakeven':  f"{atm-atm_p:,.0f}",
        'rr':         "N/A",
    }

    # ── Protective Put ────────────────────────────────────────────────
    strats['PP'] = {
        'name': "Protective Put", 'type_code': "PP", 'risk_level': "Low",
        'color_key': 'CLR_SUCCESS',
        'tags': ["[Bullish]", "[Debit]", "[Requires Underlying]"],
        'desc': "Hold the underlying, buy an ATM put as insurance. Locks in a floor while leaving upside open.",
        'legs': [f"Buy Underlying @ \u20b9{spot:,.1f}",
                 f"Buy {atm:,.0f} PE @ \u20b9{atm_p:.1f}", "", ""],
        'max_profit': "Unlimited",
        'max_loss':   _fmt_k((atm_p + max(spot-atm, 0)) * lot_size),
        'breakeven':  f"{spot+atm_p:,.0f}",
        'rr':         "N/A",
    }

    return list(strats.values())


# Directional lean of each strategy type, used to reconcile against
# _detect_traps()'s trap_str and the OI-buildup `bias` (combined_view).
# "long_vol" = direction-agnostic (profits from a big move either way), so
# it is not penalized by BULL_TRAP/BEAR_TRAP and is the one category that
# benefits from SQUEEZE rather than being dampened by it.
_STRATEGY_DIRECTION = {
    "BCS": "bullish", "BUPS": "bullish", "LC": "bullish", "CC": "bullish",
    "BPS": "bearish", "BECS": "bearish", "LP": "bearish", "RPS": "bearish",
    "IC": "neutral", "SS": "neutral", "CAL": "neutral", "BFLY": "neutral",
    "PP": "neutral",
    "LS": "long_vol", "LSG": "long_vol",
}

# Max attainable raw score per strategy code (sum of every positive branch
# for that code in _score_strategies), used to normalize scores onto a
# comparable 0-100 scale — otherwise a structurally-higher-ceiling strategy
# (e.g. SS caps at 10) always looks more "confident" than a lower-ceiling
# one (e.g. PP caps at 5) regardless of actual fit.
_STRATEGY_MAX_SCORE = {
    "BCS": 10, "IC": 10, "BPS": 9, "SS": 10, "CAL": 8, "RPS": 8,
    "CC": 7, "BFLY": 8, "BUPS": 9, "BECS": 9, "LS": 10, "LSG": 9,
    "LC": 8, "LP": 8, "PP": 5,
}


def _score_strategies(strats: list[dict], spot: float, atm: float,
                        pcr: float, iv_rank: float, dte: int,
                        bias: str = "Mixed / Neutral",
                        trap_str: str = "BALANCED",
                        trade_grade: str = "A") -> list[dict]:
    """
    Returns one dict per strategy in `strats`:
      {'score': raw int, 'confidence_pct': 0-100 normalized score after
       trap/bias reconciliation, 'veto_reasons': [str, ...]}

    Reconciliation step (this is the part that used to be missing): a
    strategy's raw rule-based score says nothing about whether it conflicts
    with the trap detector or the OI-buildup bias computed elsewhere in this
    same engine run. Without this step, a directional strategy could score
    highest and still be recommended directly into an active BULL_TRAP/
    BEAR_TRAP, or against a "Strong Bullish"/"Strong Bearish" bias — which
    is exactly the kind of mismatch that produces confident-looking losers.
    """
    results = []
    for s in strats:
        sc = 0
        code = s['type_code']
        if code == "BCS":
            if spot >= atm: sc += 3
            if pcr < T.PCR_NEUTRAL_LO: sc += 2
            if iv_rank < 50: sc += 2
            if dte > 7: sc += 1
            sc += 2
        elif code == "IC":
            if iv_rank > 60: sc += 4
            if T.PCR_NEUTRAL_LO < pcr < T.PCR_BULL: sc += 3
            if dte > 10: sc += 2
            sc += 1
        elif code == "BPS":
            if spot < atm: sc += 3
            if pcr < T.PCR_BEAR: sc += 2
            if iv_rank < 50: sc += 2
            if dte > 7: sc += 1
            sc += 1
        elif code == "SS":
            if iv_rank > 70: sc += 5
            if T.PCR_NEUTRAL_LO < pcr < T.PCR_NEUTRAL_HI: sc += 3
            if dte > 15: sc += 2
        elif code == "CAL":
            if dte < 10: sc += 4
            if iv_rank < 40: sc += 3
            sc += 1
        elif code == "RPS":
            if spot < atm: sc += 3
            if iv_rank > 55: sc += 3
            if dte > 10: sc += 2
        elif code == "CC":
            if T.PCR_NEUTRAL_LO < pcr < T.PCR_NEUTRAL_HI: sc += 2  # roughly flat-to-mildly-bullish outlook (was a stray leading "-" before T.PCR_NEUTRAL_LO, which made the lower bound -0.9 and effectively always true)
            if iv_rank > 45: sc += 3      # richer premium to sell against the holding
            if dte > 7: sc += 1
            sc += 1
        elif code == "BFLY":
            if abs(spot - atm) < dte:     # spot already pinned near ATM
                sc += 3
            if iv_rank < 45: sc += 3
            if dte < 15: sc += 2
        elif code == "BUPS":
            if spot >= atm: sc += 3
            if iv_rank > 45: sc += 3      # credit strategies want richer premium
            if pcr < 1.0: sc += 2
            if dte > 5: sc += 1
        elif code == "BECS":
            if spot < atm: sc += 3
            if iv_rank > 45: sc += 3
            if pcr > 1.0: sc += 2
            if dte > 5: sc += 1
        elif code == "LS":
            if iv_rank < 40: sc += 5      # buying vega — want it cheap
            if T.PCR_NEUTRAL_LO < pcr < T.PCR_NEUTRAL_HI: sc += 3
            if dte > 10: sc += 2
        elif code == "LSG":
            if iv_rank < 40: sc += 4
            if T.PCR_NEUTRAL_LO < pcr < T.PCR_NEUTRAL_HI: sc += 2
            if dte > 10: sc += 2
            sc += 1                       # cheaper entry than a straddle
        elif code == "LC":
            if spot >= atm: sc += 3
            if pcr < T.PCR_NEUTRAL_LO: sc += 2
            if iv_rank < 45: sc += 2      # cheaper premium to buy
            if dte > 5: sc += 1
        elif code == "LP":
            if spot < atm: sc += 3
            if pcr > T.PCR_NEUTRAL_HI: sc += 2
            if iv_rank < 45: sc += 2
            if dte > 5: sc += 1
        elif code == "PP":
            if spot >= atm: sc += 2       # already holding, protecting gains
            if iv_rank < 50: sc += 2      # cheaper insurance
            sc += 1

        direction = _STRATEGY_DIRECTION.get(code, "neutral")
        veto_reasons: list[str] = []
        multiplier = 1.0

        # ── Trap reconciliation ─────────────────────────────────────────
        if trap_str == "BULL_TRAP" and direction == "bullish":
            multiplier *= 0.25
            veto_reasons.append("Conflicts with active BULL_TRAP — chasing upside into a wall.")
        elif trap_str == "BEAR_TRAP" and direction == "bearish":
            multiplier *= 0.25
            veto_reasons.append("Conflicts with active BEAR_TRAP — chasing downside into a wall.")
        elif trap_str == "SQUEEZE" and direction in ("bullish", "bearish", "neutral"):
            # Range-bound premium sellers and one-sided directional bets are
            # both exposed to a squeeze resolving hard in either direction;
            # only long_vol strategies are structurally suited to it.
            multiplier *= 0.6
            veto_reasons.append("OI squeeze active — range/directional bets both exposed to a breakout.")

        # ── Bias (OI buildup conviction) reconciliation ─────────────────
        if bias == "Strong Bullish" and direction == "bearish":
            multiplier *= 0.35
            veto_reasons.append("OI buildup shows Strong Bullish conviction — contradicts bearish strategy.")
        elif bias == "Bullish" and direction == "bearish":
            multiplier *= 0.6
            veto_reasons.append("OI buildup leans Bullish — contradicts bearish strategy.")
        elif bias == "Strong Bearish" and direction == "bullish":
            multiplier *= 0.35
            veto_reasons.append("OI buildup shows Strong Bearish conviction — contradicts bullish strategy.")
        elif bias == "Bearish" and direction == "bullish":
            multiplier *= 0.6
            veto_reasons.append("OI buildup leans Bearish — contradicts bullish strategy.")

        # ── Overall setup risk (trade_grade) — dampens everything, since a
        # C/D grade means elevated VIX and/or multiple traps stacked, not
        # just one specific directional conflict ─────────────────────────
        if trade_grade == "C":
            multiplier *= 0.85
        elif trade_grade == "D":
            multiplier *= 0.6
            veto_reasons.append(f"Trade grade {trade_grade} — elevated overall setup risk.")

        max_score = _STRATEGY_MAX_SCORE.get(code, max(sc, 1))
        confidence_pct = round(min((sc / max_score) * 100.0, 100.0) * multiplier)

        results.append({
            'score': sc,
            'confidence_pct': confidence_pct,
            'veto_reasons': veto_reasons,
        })
    return results




# ===========================================================================
# Scenario P&L (was inline inside dashboard_modules.render_risk_dashboard /
# the now-deleted dashboard_intelligence.py duplicate)
# ===========================================================================

def _build_scenario_pnl(spot: float, atm_delta: float, lot_size: int) -> list[dict]:
    scenarios = [-0.05, -0.03, -0.01, 0.0, 0.01, 0.03, 0.05]
    labels = ["-5%", "-3%", "-1%", "Flat", "+1%", "+3%", "+5%"]
    out = []
    for label, shift in zip(labels, scenarios):
        sim_pnl = (shift * spot * atm_delta) * lot_size
        out.append({'label': label, 'shift': shift, 'pnl': sim_pnl})
    return out
