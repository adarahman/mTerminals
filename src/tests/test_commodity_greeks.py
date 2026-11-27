import math
from datetime import datetime, timedelta

import pytest

from analytics.commodity_greeks import (
    IST,
    attach_commodity_greeks,
    black76_iv,
    black76_price,
    years_to_expiry,
)
from oi.pricing import ANNUAL_RISK_FREE_RATE as R

NOW = datetime(2026, 11, 26, 20, 0, tzinfo=IST)


def test_black76_price_matches_closed_form():
    f, k, t, sigma = 100.0, 95.0, 0.5, 0.25
    d1 = (math.log(f / k) + 0.5 * sigma**2 * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    call = math.exp(-R * t) * (f * n(d1) - k * n(d2))
    put = math.exp(-R * t) * (k * n(-d2) - f * n(-d1))
    assert black76_price(f, k, t, sigma, "C") == pytest.approx(call, abs=1e-9)
    assert black76_price(f, k, t, sigma, "P") == pytest.approx(put, abs=1e-9)


@pytest.mark.parametrize("strike,opt", [(7800, "C"), (7800, "P"), (9000, "C"), (6500, "P"), (6800, "C")])
@pytest.mark.parametrize("sigma", [0.18, 0.45, 1.6])
def test_black76_iv_round_trips(strike, opt, sigma):
    f, t = 7800.0, 20 / 365
    price = black76_price(f, strike, t, sigma, opt)
    if price < 0.05:
        pytest.skip("premium below tick size")
    assert black76_iv(price, f, strike, t, opt) == pytest.approx(sigma, abs=1e-4)


def test_black76_iv_rejects_premium_below_intrinsic():
    t = 20 / 365
    assert black76_iv(50.0, 7800.0, 7600.0, t, "C") is None
    assert black76_iv(None, 7800.0, 7600.0, t, "C") is None


def test_years_to_expiry_uses_mcx_session_close():
    assert years_to_expiry("26NOV2026", NOW) == pytest.approx(3.5 / (365 * 24))
    assert years_to_expiry("25NOV2026", NOW) is None
    assert years_to_expiry("bad", NOW) is None


def _chain(sigma=0.35, days=20):
    f = 7800.0
    t = years_to_expiry((NOW + timedelta(days=days)).strftime("%d%b%Y").upper(), NOW)
    rows = []
    for k in (7700, 7800, 7900):
        for side, opt in (("CE", "C"), ("PE", "P")):
            rows.append({"strike": k, "type": side, "oi": 1000 if side == "CE" else 500,
                         "ltp": round(black76_price(f, k, t, sigma, opt), 2)})
    return {
        "spot": f,
        "atm_strike": 7800,
        "lot_size": 100,
        "expiry": (NOW + timedelta(days=days)).strftime("%d%b%Y").upper(),
        "rows": rows,
    }, t


def test_attach_commodity_greeks_solves_iv_and_builds_greeks():
    chain, t = _chain()
    attach_commodity_greeks(chain, now=NOW)

    assert chain["atm_iv"] == pytest.approx(35, abs=0.05)
    assert all(row["iv"] == pytest.approx(35, abs=0.1) for row in chain["rows"])
    assert [g["strike"] for g in chain["greeks"]] == [7700, 7800, 7900]
    atm = chain["greeks"][1]
    # Black-76 put-call delta parity: cDelta - pDelta = e^{-rT}
    assert atm["cDelta"] - atm["pDelta"] == pytest.approx(math.exp(-R * t), abs=2e-4)
    assert 0.45 < atm["cDelta"] < 0.55
    assert atm["cGamma"] > 0 and atm["cTheta"] < 0 and atm["cVega"] > 0
    assert atm["netGEX"] > 0  # CE OI > PE OI at equal gamma


def test_attach_commodity_greeks_without_prices_returns_empty():
    chain = {"spot": 7800, "expiry": "31DEC2099",
             "rows": [{"strike": 7800, "type": "CE", "oi": 10}]}
    attach_commodity_greeks(chain, now=NOW)
    assert chain["greeks"] == [] and chain["atm_iv"] is None
    assert "iv" not in chain["rows"][0]


def test_attach_commodity_greeks_after_expiry_returns_empty():
    chain, _ = _chain()
    chain["expiry"] = "01JAN2020"
    attach_commodity_greeks(chain, now=NOW)
    assert chain["greeks"] == [] and chain["atm_iv"] is None
