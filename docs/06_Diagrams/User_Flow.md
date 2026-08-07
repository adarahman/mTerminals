# Trader User Flow


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


```mermaid
graph LR
  OPEN[Open Dashboard] --> VALID[Validate Feed]
  VALID --> DECIDE[Read Decision]
  DECIDE --> VERIFY[Scan Structure/Flow]
  VERIFY --> DRILL[Drill to Chain/Strike]
  DRILL --> SCEN[Scenario if needed]
  SCEN --> EXEC[Paper/Live execution path]
  EXEC --> MON[Monitor]
```
