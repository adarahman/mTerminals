# Metric Dependency

> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

```mermaid
flowchart TD
  PRIM["Chain primitives: price · OI · ΔOI · volume · IV"] --> PCR["PCR / walls / max pain"]
  PRIM --> CAP1["Stage-1 capital: locked · flow · turnover · notional"]
  PRIM --> GREEKS["Verified live Greeks"]
  GREEKS --> GEX["Net GEX / gamma flip"]
  GREEKS --> CAP2["Nullable delta / gamma exposure"]
  PRIM --> VEL["5m / 15m / 30m OI velocity"]
  CAP1 --> FOOT["Strike footprint and concentration"]
  CASH["Lagged FII/DII cash + participant F&O"] --> INST["Institutional context"]
  FUT["Futures basis / buildup"] --> CONFIRM["Futures confirmation"]
  PCR --> DEC["DecisionEngine"]
  GEX --> DEC
  VEL --> DEC
  FOOT --> DEC
  INST --> DEC
  CONFIRM --> DEC
  VOL["VIX / IV / HV regime"] --> DEC
  DEC --> OUT["Bias · action · grade · evidence confidence · warnings"]
  GREEKS --> SCEN["Scenario namespace"]
  VOL --> SCEN
  SCEN -. "never mutates live state" .-> OUT
```

Arrows mean calculation dependency, not visual duplication. Canonical owners
compute each shared metric once; views format and explain those values.
