import math

import pandas as pd

from oi.capital_metrics import compute_capital_metrics, compute_chain_metrics


def _master(ce_iv=20.0, pe_iv=21.0):
    return pd.DataFrame([{
        "strike": 100.0, "ce_oi": 100.0, "pe_oi": 120.0,
        "ce_ltp": 10.0, "pe_ltp": 12.0,
        "ce_oi_chg": 5.0, "pe_oi_chg": -4.0,
        "ce_volume": 2.0, "pe_volume": 3.0,
        "ce_iv": ce_iv, "pe_iv": pe_iv,
        "ce_delta": 0.5, "pe_delta": -0.4,
        "ce_gamma": 0.01, "pe_gamma": 0.02,
    }])


def test_oi_quantity_is_not_lot_scaled_twice_but_volume_is_converted():
    result = compute_capital_metrics(_master(), spot=105.0, lot_size=50).iloc[0]
    assert result.ce_premium_locked == 100.0 * 10.0
    assert result.ce_capital_flow == 5.0 * 10.0
    assert result.ce_notional_exposure == 100.0 * 100.0
    assert result.ce_premium_turnover == 2.0 * 50.0 * 10.0


def test_missing_greeks_produce_nullable_stage_two_exposure():
    result = compute_capital_metrics(_master(ce_iv=0.0, pe_iv=0.0), spot=105.0, lot_size=50)
    row = result.iloc[0]
    assert math.isnan(row.ce_delta_exposure)
    assert math.isnan(row.pe_gamma_exposure)
    summary = compute_chain_metrics(result)
    assert summary["net_delta_exposure"] is None
    assert summary["net_gamma_exposure"] is None


def test_verified_greeks_keep_real_zero_distinct_from_unavailable():
    master = _master()
    master.loc[0, ["ce_delta", "pe_delta", "ce_gamma", "pe_gamma"]] = 0.0
    result = compute_capital_metrics(master, spot=105.0, lot_size=50)
    summary = compute_chain_metrics(result)
    assert summary["net_delta_exposure"] == 0.0
    assert summary["net_gamma_exposure"] == 0.0


def test_capital_walls_respect_spot_side_instead_of_selecting_itm_premium():
    rows = []
    for strike, ce_ltp, pe_ltp in [
        (100.0, 30.0, 1.0),
        (110.0, 12.0, 8.0),
        (120.0, 2.0, 25.0),
    ]:
        row = _master().iloc[0].to_dict()
        row.update(strike=strike, ce_ltp=ce_ltp, pe_ltp=pe_ltp)
        rows.append(row)

    result = compute_capital_metrics(pd.DataFrame(rows), spot=115.0, lot_size=50)
    summary = compute_chain_metrics(result, spot=115.0)

    assert summary["ce_capital_wall_strike"] == 120.0
    assert summary["pe_capital_wall_strike"] == 110.0
