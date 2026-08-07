# State Management


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## State categories

### Market state
Canonical live data, owned client-side by `MarketStore`.

### View context
Symbol, expiry, range and navigation context.

### Derived analytical state
Canonical metrics/decision outputs received or computed through approved domain paths.

### Interaction state
Open modal, expanded strike, collapsed zone, drag state, chart viewport.

### Account state
Paper/live positions, orders and funds; separate from market analytics.

## Critical rule

A market tick SHALL NOT overwrite interaction state.

## Feed lifecycle

```text
INITIAL → CONNECTING → LIVE
                    ↘ PARTIAL
LIVE → STALE → DISCONNECTED → RECOVERING → LIVE
```

## Full vs delta

- Full snapshot establishes/re-establishes baseline.
- Delta applies only against a compatible baseline.
- Keyed arrays such as option-chain strikes SHOULD merge by stable key.
- Recovery SHOULD obtain/reconcile a coherent snapshot before declaring live.

## Persistence

v1 product rules:
- global/runtime state may persist as implementation needs;
- Dashboard collapse state does not persist across page reload unless a future PDS says so;
- paper account persistence follows its own contract.

## No dual truth

`window.*` legacy shims MAY exist during migration but SHALL not become independent
stores that drift from MarketStore/AppState.
