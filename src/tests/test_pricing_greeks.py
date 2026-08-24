import numpy as np
import pandas as pd
from decision.engine import OptionChainEngine
from oi.pricing import (
    bs_charm,
    bs_delta,
    bs_gamma,
    bs_greeks_vectorized,
    bs_rho,
    bs_theta,
    bs_vanna,
    bs_vega,
)


def _scalar_greeks(spot, strike, t, rate, dividend_yield, sigma, side):
    option_type = "C" if side == "CE" else "P"
    return np.array([
        bs_delta(spot, strike, t, rate, sigma, option_type, dividend_yield),
        bs_gamma(spot, strike, t, rate, sigma, dividend_yield),
        bs_theta(spot, strike, t, rate, sigma, option_type, dividend_yield),
        bs_vega(spot, strike, t, rate, sigma, dividend_yield),
        bs_rho(spot, strike, t, rate, sigma, option_type, dividend_yield),
        bs_charm(spot, strike, t, rate, sigma, option_type, dividend_yield),
        bs_vanna(spot, strike, t, rate, sigma, dividend_yield),
    ])


def test_vectorized_greeks_match_scalar_source_of_truth():
    spot, rate, dividend_yield = 24_000.0, 0.07, 0.0123
    strikes = np.array([23_500.0, 24_000.0, 24_500.0])
    times = np.array([1.0, 7.0, 30.0]) / 365.0
    sigmas = np.array([0.12, 0.16, 0.21])

    for side in ("CE", "PE"):
        batch = np.stack(
            bs_greeks_vectorized(
                spot, strikes, times, rate, dividend_yield, sigmas, side
            ),
            axis=1,
        )
        expected = np.stack([
            _scalar_greeks(
                spot, strike, t, rate, dividend_yield, sigma, side
            )
            for strike, t, sigma in zip(strikes, times, sigmas)
        ])
        np.testing.assert_allclose(batch, expected, rtol=1e-12, atol=1e-12)


def test_vectorized_greeks_zero_every_invalid_row():
    result = bs_greeks_vectorized(
        24_000.0,
        [0.0, 24_000.0, 24_000.0, np.nan],
        [1 / 365, 0.0, 1 / 365, 1 / 365],
        0.07,
        0.0123,
        [0.16, 0.16, 0.0, 0.16],
        "CE",
    )

    for greek in result:
        np.testing.assert_array_equal(greek, np.zeros(4))


def test_compatibility_engine_uses_canonical_batch_values():
    chain = pd.DataFrame({
        "StrikePrice": [23_900.0, 24_000.0, 24_100.0],
        "CE_OI": [100, 200, 300],
        "PE_OI": [300, 200, 100],
    })
    engine = OptionChainEngine(spot=24_000.0, dte=7, base_iv=0.16)
    enriched = engine.enrich(chain)
    strikes = chain["StrikePrice"].to_numpy()
    times = np.full(3, engine.t)
    iv = enriched["CE_IV_adj"].to_numpy()
    ce = bs_greeks_vectorized(24_000.0, strikes, times, 0.07, 0.0, iv, "CE")
    pe = bs_greeks_vectorized(24_000.0, strikes, times, 0.07, 0.0, iv, "PE")

    np.testing.assert_allclose(enriched["CE_Delta"], ce[0])
    np.testing.assert_allclose(enriched["CE_Gamma"], ce[1])
    np.testing.assert_allclose(enriched["CE_Theta"], ce[2])
    np.testing.assert_allclose(enriched["CE_Vega"], ce[3])
    np.testing.assert_allclose(enriched["PE_Delta"], pe[0])
    np.testing.assert_allclose(enriched["PE_Gamma"], pe[1])
    np.testing.assert_allclose(enriched["PE_Theta"], pe[2])
    np.testing.assert_allclose(enriched["PE_Vega"], pe[3])
