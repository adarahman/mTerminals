# PDS-03 — Strike Detail Report


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Mission

Answer one question:

> **Why is this specific strike important?**

This report is a Tier-3 investigation surface reached from D-12, D-05 or another
explicit strike drill-down.

## Required context

- Symbol.
- Expiry.
- Strike.
- Spot/ATM relation.
- Data timestamp and feed state.

## Required sections

1. **Strike summary** — moneyness, call/put state, importance label.
2. **Positioning** — OI, ΔOI, volume, Vol/OI.
3. **Greeks** — Delta, Gamma, Theta, Vega and IV where available.
4. **Capital** — notional exposure, premium locked/turnover and approved capital metrics.
5. **Institutional primitive** — the per-strike score/signals with contributor explanation.
6. **Flow interpretation** — build/unwind direction and velocity.
7. **Scenario sensitivity** — only when explicitly qualified as scenario-derived.

## Ownership rules

The report consumes canonical values. It is not another analytics engine.
If it derives a strike-specific explanation, the derivation SHALL be documented
and SHALL not redefine shared metrics.

## Interaction

- Back/close returns to the originating surface and restores focus/context.
- Navigating to adjacent strikes MAY be supported without losing expiry.
- A new live tick updates displayed values without resetting the user's active tab/section.

## Failure behavior

If a component such as Greeks is missing, only that section degrades.
The report remains useful using available evidence.

## Acceptance

The report is correct when a trader can explain why a strike was highlighted
without consulting a second source for basic strike primitives.
