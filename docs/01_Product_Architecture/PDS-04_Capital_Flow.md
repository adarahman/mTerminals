# PDS-04 — Capital Flow


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Mission

Answer:

> **Where is meaningful money moving now, not merely where old OI already sits?**

Capital Flow separates static positioning from active participation.

## Current analytical foundations

The current backend contains `oi/capital_metrics.py`,
`analytics/capital_futures_confirmation.py`, FII/DII analytics, OI analysis and
futures OI tracking. The UI contains OI Flow and FII/DII reports.

## Core metric families

### Stage 1 — approved display foundation
- capital flow;
- notional exposure;
- premium locked;
- premium turnover;
- OI build/unwind;
- Vol/OI velocity.

### Stage 2 — conditional on verified live Greeks
- delta exposure;
- gamma exposure.

### Stage 3 — interpretive scores
Institutional/support/resistance scores SHALL remain separately calibrated and
must not silently enter live decisioning merely because they are displayed.

## Product separation

**D-07** owns fresh derivatives flow interpretation.  
**D-08** owns institutional cash-market flow.  
**D-11** owns capital concentration/walls.

These are related but not interchangeable.

## Per-strike capital contract

The implementation SHALL document units. Where the backend master table already
stores lot-scaled OI, downstream functions SHALL NOT multiply by lot size again.

Volume contract counts and OI quantity terms SHALL not be mixed without explicit
conversion.

## Flow language

UI labels SHALL distinguish:
- concentration;
- addition;
- unwinding;
- turnover;
- directional interpretation.

A large existing OI wall is not automatically "fresh buying" or "fresh writing."

## Futures confirmation

Futures OI confirmation is supporting evidence. It SHOULD qualify an option-flow
interpretation rather than overwrite it.

## Acceptance

1. Static OI concentration and current flow cannot be confused visually.
2. Units are defined for every monetary metric.
3. Duplicate lot-size multiplication is impossible in the approved path.
4. Stage-2 metrics show unavailable when verified Greeks are missing.
5. FII/DII cash flow is explicitly labeled as cash-market evidence.
