# System Overview


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


```mermaid
graph LR
  EX[SmartAPI / NSE] --> BE[Backend Acquisition]
  BE --> AN[OI + Analytics]
  AN --> DE[Decision Engine]
  DE --> RK[Risk / Execution]
  AN --> WS[WebSocket / JSON]
  DE --> WS
  WS --> MS[MarketStore]
  MS --> DB[Dashboard]
  MS --> OC[Option Chain]
  MS --> PC[Price Chart]
  MS --> PT[Paper Trading UI]
```
