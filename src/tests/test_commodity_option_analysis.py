from analytics.commodity_option_analysis import analyze_commodity_option_chain


def _chain(call_oi, put_oi, futures_change):
    return {
        "future_change_pct": futures_change,
        "rows": [
            {"type": "CE", "strike": 6000, "oi": call_oi, "volume": 10},
            {"type": "PE", "strike": 6000, "oi": put_oi, "volume": 20},
            {"type": "CE", "strike": 6100, "oi": 50, "volume": 5},
            {"type": "PE", "strike": 5900, "oi": 75, "volume": 8},
        ],
    }


def test_commodity_analysis_is_bullish_when_positioning_and_futures_agree():
    result = analyze_commodity_option_chain(_chain(100, 150, 0.4))

    assert result["verdict"] == "BULLISH"
    assert result["put_call_oi_ratio"] == 1.5
    assert result["evidence_coverage"] == 43
    assert result["confidence"] > 0
    assert "engine_bias" in result["missing_inputs"]
    assert "oi_velocity" in result["missing_inputs"]
    contributors = {item["key"]: item for item in result["evidence_weights"]}
    assert contributors["pcr"]["weight"] == 25
    assert contributors["pcr"]["direction"] == "BULLISH"
    assert contributors["futures"]["weight"] == 18
    assert contributors["futures"]["direction"] == "BULLISH"
    assert contributors["engine_bias"]["available"] is False
    assert result["call_oi_wall"] == 6000
    assert result["put_oi_wall"] == 6000


def test_commodity_analysis_is_bearish_when_positioning_and_futures_agree():
    result = analyze_commodity_option_chain(_chain(150, 100, -0.4))

    assert result["verdict"] == "BEARISH"
    assert result["put_call_oi_ratio"] == 0.88


def test_commodity_analysis_waits_for_missing_inputs_and_marks_conflicts_neutral():
    missing = analyze_commodity_option_chain(_chain(100, 150, None))
    conflict = analyze_commodity_option_chain(_chain(100, 150, -0.4))

    assert missing["verdict"] == "WAIT"
    missing_factors = {item["key"]: item for item in missing["evidence_weights"]}
    assert missing_factors["pcr"]["direction"] == "BULLISH"
    assert missing_factors["futures"]["direction"] == "UNAVAILABLE"
    assert conflict["verdict"] == "NEUTRAL"
    conflict_factors = {item["key"]: item for item in conflict["evidence_weights"]}
    assert conflict_factors["pcr"]["direction"] == "BULLISH"
    assert conflict_factors["futures"]["direction"] == "BEARISH"
    assert conflict["confidence"] <= 40
    assert "current positioning" in conflict["scope"]
