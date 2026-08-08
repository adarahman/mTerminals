# Metric Ownership


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Principle

Each shared metric has:
1. a **domain computation owner**; and
2. a **canonical presentation owner**.

Other displays are read-only consumers.

| Metric | Domain owner | Canonical Dashboard display | Consumers |
|---|---|---|---|
| Spot/context | market/feed | D-00 | all |
| Decision bias | `decision/` | D-01 | supporting surfaces |
| Confidence | `decision/confidence.py` | D-01 | supporting surfaces |
| Trade grade | `decision/` | D-01 | supporting surfaces |
| PCR | `oi/chain_metrics.py` or approved chain layer | D-04 | D-01,D-02,D-14 |
| Max Pain | approved OI analytics | D-04 | D-01,D-02 |
| Live Net GEX | Greeks/OI analytics | D-03 | D-01,D-09,D-16 |
| Live Gamma Flip | Greeks/OI analytics | D-03 | D-01,D-02 |
| Per-strike Greeks | broker/Greeks source + normalized chain | D-05 | D-03,D-06,D-16 |
| OI flow/velocity | OI analytics | D-07 | D-01,D-02,D-09 |
| FII/DII flow | `analytics/` | D-08 | D-01,D-09 |
| Market regime | `analytics/market_regime.py` | D-09 | D-01,D-02 |
| Smart Money whole-chain | approved analytics | D-09 | D-01,D-14 |
| Institutional per-strike primitive | `oi/footprint_score.py` / approved source | D-05 | D-10,D-12 |
| Capital concentration/walls | `oi/capital_metrics.py` | D-11 | D-01,D-12 |
| Notional exposure | `oi/capital_metrics.py` | D-11/Capital detail | consumers |
| Premium locked | `oi/capital_metrics.py` | Capital detail | consumers |
| Premium turnover | `oi/capital_metrics.py` | D-07/Capital detail | consumers |
| Volatility confirmation | volatility analytics | D-13 | D-01 |
| Scenario-adjusted metrics | scenario engine | D-15 | scenario only |

## Rules

- Formatting may differ; meaning may not.
- Scenario metrics use qualified names.
- Missing canonical value is `unavailable`, not locally approximated under the same label.
- Any ownership change requires PDS/architecture revision.

## Enforcement status

Decision, capital-flow, scenario, strike-detail and price-chart contract suites
verify canonical owners and reject presentation-layer approximations under the
same metric names. Backend dependency tests protect domain ownership boundaries.
