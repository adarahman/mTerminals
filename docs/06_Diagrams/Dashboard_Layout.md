# Dashboard Layout

> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

```mermaid
flowchart TD
  STATUS["D-00 Status"] --> DECISION["D-01 Decision"]
  DECISION --> STRUCT["STRUCTURE: D-02 Story · D-03 GEX · D-04 Chain snapshot"]
  STRUCT --> MONEY["D-06 Greeks by Moneyness"]
  MONEY --> CAPITAL["CAPITAL FLOW: D-07 OI Flow · D-08 FII/DII"]
  CAPITAL --> INST["INSTITUTIONAL: D-09 Regime · D-10 Footprint · D-11 Concentration"]
  INST --> CRUX["D-12 Activity Crux"]
  CRUX --> CONF["CONFIRMATION: D-13 Volatility → D-14 Probability → D-15 Scenario → D-16 Advanced → D-17 Simulator"]
  CONF --> UTIL["Persistent utilities: D-18 Paper Trading · D-19 Price Chart link"]
  STRUCT -. "deliberate drill-down" .-> CHAIN["D-05 Dedicated Option Chain"]
```

Runtime values do not reorder zones or cards. Below desktop width the same
reading order stacks vertically; compact mode does not hide required content.
