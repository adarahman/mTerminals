# Component Tree


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


```mermaid
graph TD
  APP[Dashboard Bootstrap] --> PM[PanelManager]
  APP --> WS[WSManager]
  WS --> MS[MarketStore]
  PM --> OP[OptionChainPanel]
  PM --> DP[DecisionPanel]
  PM --> OI[OI Panel]
  PM --> PP[PaperTradingPanel]
  MS --> OP
  MS --> DP
  MS --> OI
```
