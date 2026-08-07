# Metric Dependency


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


```mermaid
graph TD
  OI[OI/Chain] --> PCR[PCR]
  OI --> CAP[Capital Metrics]
  G[Greeks] --> GEX[Net GEX / Flip]
  FLOW[Flow] --> DEC[Decision]
  PCR --> DEC
  CAP --> DEC
  GEX --> DEC
  INST[Institutional] --> DEC
  VOL[Volatility] --> DEC
  DEC --> CONF[Bias + Confidence]
```
