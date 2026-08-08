# Greeks Model

> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

## Primitives and units

| Primitive | Meaning and canonical unit |
|---|---|
| Delta | option-price change per underlying price unit; PE is signed negative |
| Gamma | delta change per underlying price unit |
| Theta | INR decay per day per lot in aggregate ATM displays |
| Vega | INR change per volatility point per lot in aggregate ATM displays |
| IV | annualized volatility in percentage points |

Every set belongs to a symbol, expiry, strike, option side, observation time and
source. Broker-supplied Greeks remain broker-supplied after parsing. Canonical
engine calculations SHALL be labeled as calculated, not broker-observed.

## Exposure and availability

Delta and gamma exposure require positive OI, verified IV and a valid Greek.
Unavailable exposure is JSON `null`; a verified mathematical zero remains zero.
Whole-chain Stage-2 totals fail closed to null when any included row is
unverified.

Live Greeks and scenario-adjusted Greeks are separate namespaces and labels.
Scenario values SHALL NOT overwrite the live baseline or feed decisions back
into canonical market state.

**Owner:** canonical analytics engine. **Approved consumers:** exporter,
Dashboard Greek views, strike detail, strategies and scenario engine.
