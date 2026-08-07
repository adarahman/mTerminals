# Capital Model


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Core Stage-1 metrics

- Notional exposure.
- Premium locked.
- Premium turnover.
- Capital flow.
- Capital concentration/walls.

## Unit invariant

Current OI master data may already be lot-scaled. Capital functions SHALL consume
the documented normalized unit and SHALL NOT multiply by lot size a second time.

Raw option volume may remain contract count; any conversion must be explicit.

## Interpretation

Capital concentration = where money is positioned.
Capital flow = where meaningful money is moving.
They are not synonyms.
