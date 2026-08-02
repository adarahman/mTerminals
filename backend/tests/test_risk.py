"""Unit tests for risk/risk_meters.py's _build_risk_meters()."""

from risk.risk_meters import _build_risk_meters


def _by_name(meters, name):
    return next(m for m in meters if m["name"] == name)


def test_returns_seven_named_meters():
    meters = _build_risk_meters(atm_delta=0.5, atm_gamma=0.01, base_iv=0.15,
                                 atm_theta=-5.0, lot_size=50, dte=10, pcr=1.0)
    names = {m["name"] for m in meters}
    assert names == {
        "Delta Risk", "Gamma Risk", "Vega Risk", "Theta Decay",
        "Liquidity Risk", "Event Risk", "Concentration",
    }


def test_all_pct_values_are_clamped_0_to_100():
    # Deliberately extreme inputs to try to blow past the clamp.
    meters = _build_risk_meters(atm_delta=50.0, atm_gamma=999.0, base_iv=999.0,
                                 atm_theta=-9999.0, lot_size=1000, dte=0, pcr=0.001)
    for m in meters:
        assert 0 <= m["pct"] <= 100, f"{m['name']} out of bounds: {m['pct']}"


def test_liquidity_risk_reflects_lot_size():
    small_lot = _build_risk_meters(0.5, 0.01, 0.15, -5.0, lot_size=50, dte=10, pcr=1.0)
    big_lot = _build_risk_meters(0.5, 0.01, 0.15, -5.0, lot_size=500, dte=10, pcr=1.0)
    assert _by_name(small_lot, "Liquidity Risk")["pct"] == 25
    assert _by_name(big_lot, "Liquidity Risk")["pct"] == 60


def test_event_risk_scales_with_dte():
    near = _build_risk_meters(0.5, 0.01, 0.15, -5.0, lot_size=50, dte=1, pcr=1.0)
    mid = _build_risk_meters(0.5, 0.01, 0.15, -5.0, lot_size=50, dte=5, pcr=1.0)
    far = _build_risk_meters(0.5, 0.01, 0.15, -5.0, lot_size=50, dte=30, pcr=1.0)
    assert _by_name(near, "Event Risk")["pct"] == 85
    assert _by_name(mid, "Event Risk")["pct"] == 55
    assert _by_name(far, "Event Risk")["pct"] == 30
