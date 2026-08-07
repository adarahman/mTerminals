# Feed Model


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Canonical feed envelope

A transport payload SHOULD identify:
- type (`full`, `delta`, status);
- timestamp;
- symbol/context;
- state/snapshot version where practical;
- payload data.

## Client states

connecting, live, partial, stale, disconnected, recovering.

## Delta rules

A delta applies only to a compatible baseline. Keyed collections (option chain)
merge by stable key such as strike/expiry.

## Freshness

Freshness tolerance is a product/domain setting, not inferred from UI animation.
