# Institutional Model

> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

Institutional analytics infer significance; they do not prove participant
identity unless a source explicitly identifies it.

## Families and provenance

| Family | Source/scope | Canonical interpretation |
|---|---|---|
| FII/DII cash flow | official daily cash-market series | lagged institutional cash activity |
| F&O participant positioning | official participant-wise EOD OI | lagged positioning sentiment |
| Market regime | canonical market and volatility metrics | contextual classification |
| Strike footprint | percentile-ranked OI, ΔOI, turnover and capital activity | relative significance in the current chain |
| Capital concentration | premium locked by strike | where capital is concentrated |
| Futures confirmation | futures/options comparison | confirmation or divergence, not causation |

Unavailable or lagged data SHALL be labeled. FII/DII cash and F&O scopes SHALL
not be merged without preserving their source date and evidence coverage.
Approved language includes `institutional footprint`, `institutionally
significant`, and `smart-money interpretation`. A view SHALL NOT claim that a
specific institution placed a specific option trade without direct evidence.

**Owners:** institutional analytics and footprint modules. **Approved
consumers:** Executive, institutional report, strike detail and decision
evidence (with degradation metadata).
