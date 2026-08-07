# PDS-06 — Scenario Analysis


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Mission

Answer:

> **What happens to the thesis or strategy if price, volatility, time or
> positioning assumptions change?**

Scenario Analysis is Tier 3 and SHALL never be confused with live state.

## Scenario dimensions

MAY include:
- underlying price shift;
- volatility shift;
- time decay;
- selected strike/expiry changes;
- strategy legs;
- alternative positioning assumptions where modelled.

## Naming rule

Every scenario-derived shared metric SHALL carry a qualifier such as:
`Scenario`, `Projected`, `Adjusted`, or the scenario name.

Example:
- `Live Gamma Flip`
- `Scenario-Adjusted Gamma Flip`

## Isolation

Scenario state is local analytical state. It SHALL NOT overwrite:
- live MarketStore state;
- dashboard canonical live metrics;
- Decision Engine live evidence;
- paper/live positions.

## Reproducibility

A scenario SHOULD be representable by explicit inputs so results can be
recomputed and compared.

## Acceptance

1. Running a scenario does not mutate live dashboard metrics.
2. Scenario labels cannot be mistaken for live values.
3. Reset restores scenario inputs without forcing a market-data reload.
4. Live ticks may update reference values without erasing user-entered scenario inputs.
