# Dependency Graph


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Backend target dependency direction

```mermaid
graph TD
  B[brokers / market APIs] --> S[storage/cache]
  B --> OI[oi]
  S --> OI
  OI --> A[analytics]
  A --> D[decision]
  OI --> D
  D --> ST[strategy selection]
  D --> R[risk/execution gating]
  ST --> R
```

Infrastructure MAY be consumed by multiple layers, but reverse dependencies
such as `storage → decision` are prohibited.

## Frontend target dependency direction

```mermaid
graph TD
  WS[WSManager] --> MS[MarketStore]
  MS --> VM[View Models]
  AS[AppState] --> VM
  VM --> P[Panels / Views]
  P --> DOM[DOM / Canvas]
  EB[EventBus] --> AS
  P --> EB
```

Events do not replace MarketStore.

## Architectural smell checks

Flag any new dependency where:
- broker imports strategy;
- storage imports decision;
- risk is used as a calculator by UI;
- a card imports another card only to read a value;
- template code computes a business metric that already exists in backend/domain logic.
