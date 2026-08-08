# Capital Model

> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

## Stage-1 metrics

| Metric | Formula | Unit | Missing behavior |
|---|---|---|---|
| Strike notional | normalized OI × strike | INR | null per unusable strike |
| Premium locked | normalized OI × LTP | INR | null per unusable strike |
| Capital flow | day-session ΔOI × LTP | INR | null per unusable strike |
| Premium turnover | raw volume × lot size × LTP | INR | null per unusable strike |
| Capital wall | strike with maximum premium locked per side | strike | null if no usable rows |

Normalized OI is already lot-scaled underlying quantity. Only raw volume gets a
lot-size multiplication. Capital concentration describes where money is
positioned; capital flow describes where meaningful money is moving. They are
not synonyms. Chain Stage-1 totals may skip unavailable strikes, and consumers
must disclose visible-range versus whole-chain scope.

## Stage-2 exposure

- Delta exposure = normalized OI × signed delta × spot.
- Gamma exposure = normalized OI × gamma × spot².
- PE delta is signed negative, so net delta is CE + PE.
- Both leg gammas are positive magnitudes, so net gamma is CE − PE.

Stage-2 values require verified IV and Greeks. If any included strike is
unverified, the chain total is `null`, not a partial total or manufactured zero.

**Owner:** `backend/oi/capital_metrics.py`. **Approved consumers:** exporter,
Executive view, Capital Flow, strike detail and Smart Money interpretation.
