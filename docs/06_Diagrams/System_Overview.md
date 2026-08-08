# System Overview

> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

```mermaid
flowchart LR
  NSE["NSE / BSE REST"] --> ACQ["Market acquisition"]
  API["Angel One SmartAPI"] --> ACQ
  ACQ --> CACHE["Runtime cache and history"]
  ACQ --> DOMAIN["Canonical option, OI, Greeks and capital analytics"]
  CACHE --> DOMAIN
  DOMAIN --> DEC["Decision engine"]
  DOMAIN --> EXPORT["Versioned JSON snapshot"]
  DEC --> EXPORT
  DEC --> RISK["Risk guard and execution gate"]
  RISK --> BROKER["Paper engine or explicitly enabled live broker"]
  EXPORT --> WS["Full / delta WebSocket transport"]
  WS --> STORE["MarketStore + AppState"]
  STORE --> DASH["Dashboard"]
  STORE --> CHAIN["Option Chain"]
  STORE --> CHART["Price Chart"]
  STORE --> PAPER["Paper Trading UI"]
```

Dependency direction is left to right. Presentation never calls acquisition or
domain modules directly; live execution remains behind risk and explicit mode
gates.
