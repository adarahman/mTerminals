from types import SimpleNamespace

from application.payload_builders.risk import build_risk


def test_build_risk_preserves_dashboard_shape_and_grade():
    payload = build_risk(
        {
            "base_iv": 0.20,
            "hv30": 15,
            "ce_wall": 25100,
            "pe_wall": 24900,
            "max_pain": 25000,
            "atm": 25000,
        },
        SimpleNamespace(trade_grade="A"),
    )

    assert payload["tradeGrade"] == "A"
    assert payload["ivRegime"] == "Rich"
    assert payload["ivHvSpread"] == 5.0
    assert [level["value"] for level in payload["keyLevels"]] == [25100, 24900, 25000, 25000]
