"""Dashboard signal-panel payload builder."""
from __future__ import annotations

import math

from application.payload_builders.common import safe_string


def _number(value, decimals=2) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(parsed) or math.isinf(parsed):
        return 0.0
    return round(parsed, decimals)


def _bias_class(value: str) -> str:
    normalized = value.lower()
    if "bull" in normalized or "long" in normalized:
        return "bull"
    if "bear" in normalized or "short" in normalized:
        return "bear"
    return "neutral"


def build_signals(context: dict, engine_result=None) -> list[dict]:
    futures = safe_string(context.get("fut_signal"))
    vix_regime = safe_string(context.get("vix_regime"))
    trap = safe_string(context.get("trap_warn"))
    pcr_sentiment = safe_string(context.get("pcr_sentiment"))
    spot_change = _number(context.get("spot_chg_pct", 0.0))

    signals = [
        {
            "label": "Futures signal",
            "value": futures,
            "cls": _bias_class(futures),
        },
        {
            "label": "PCR sentiment",
            "value": pcr_sentiment,
            "cls": _bias_class(pcr_sentiment),
        },
        {
            "label": "VIX regime",
            "value": vix_regime,
            "cls": (
                "bull"
                if "low" in vix_regime.lower()
                else "bear"
                if "high" in vix_regime.lower()
                else "neutral"
            ),
        },
        {
            "label": "Spot change",
            "value": f"{'+' if spot_change >= 0 else ''}{spot_change:.2f}%",
            "cls": (
                "bull"
                if spot_change > 0
                else "bear"
                if spot_change < 0
                else "neutral"
            ),
        },
        {
            "label": "Trap warning",
            "value": trap,
            "cls": (
                "bear"
                if trap.lower() not in {"none", "—", ""}
                else "neutral"
            ),
        },
    ]

    if engine_result is not None:
        for attribute in ("extra_signals", "signals"):
            extras = getattr(engine_result, attribute, None)
            if isinstance(extras, list):
                for signal in extras:
                    if not (
                        isinstance(signal, dict)
                        and "label" in signal
                        and "value" in signal
                    ):
                        continue
                    signals.append(
                        {
                            "label": safe_string(signal.get("label")),
                            "value": safe_string(signal.get("value")),
                            "cls": safe_string(
                                signal.get("cls", "neutral")
                            ),
                        }
                    )
                break
    return signals
