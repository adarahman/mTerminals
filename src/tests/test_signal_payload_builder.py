from types import SimpleNamespace

from application.payload_builders.signals import build_signals


def test_signal_builder_formats_core_and_extra_signals():
    result = build_signals(
        {
            "fut_signal": "Long buildup",
            "pcr_sentiment": "Bearish",
            "vix_regime": "Low volatility",
            "spot_chg_pct": 1.234,
            "trap_warn": "None",
        },
        SimpleNamespace(
            extra_signals=[
                {"label": "Flow", "value": "Positive", "cls": "bull"}
            ]
        ),
    )

    assert result[0] == {
        "label": "Futures signal",
        "value": "Long buildup",
        "cls": "bull",
    }
    assert result[3]["value"] == "+1.23%"
    assert result[-1]["label"] == "Flow"
