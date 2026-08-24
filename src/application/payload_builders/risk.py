"""Risk-panel payload construction."""

from oi.pricing import DEFAULT_BASE_IV

from .common import integer, rounded_number, safe_string


def build_risk(ctx_dict, engine_result=None):
    iv_rank = rounded_number(ctx_dict.get("iv_rank", 35.0), 1)
    atm_iv = rounded_number(ctx_dict.get("base_iv", DEFAULT_BASE_IV) * 100, 2)
    hv30 = rounded_number(ctx_dict.get("hv30", 15.0), 1)
    iv_hv = atm_iv - hv30
    iv_regime = "Rich" if iv_hv > 3 else ("Cheap" if iv_hv < -2 else "Fair")
    trade_grade = "—"
    if engine_result is not None:
        trade_grade = safe_string(
            getattr(engine_result, "trade_grade", None)
            or ctx_dict.get("trade_grade", "—")
        )
    return {
        "tradeGrade": trade_grade,
        "trapWarn": safe_string(ctx_dict.get("trap_warn", "None")),
        "ivRegime": iv_regime,
        "ivHvSpread": rounded_number(iv_hv, 2),
        "keyLevels": [
            {"label": "CE wall", "value": integer(ctx_dict.get("ce_wall", 0)), "cls": "bear"},
            {"label": "PE wall", "value": integer(ctx_dict.get("pe_wall", 0)), "cls": "bull"},
            {"label": "Max pain", "value": integer(ctx_dict.get("max_pain", 0)), "cls": "neutral"},
            {"label": "ATM", "value": integer(ctx_dict.get("atm", 0)), "cls": "neutral"},
        ],
    }
