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
    assert conflict["verdict"] == "NEUTRAL"
    assert "current positioning" in conflict["scope"]
