# Rendering Pipeline


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


```mermaid
graph LR
  M[WS Message] --> W[WSManager]
  W --> S[MarketStore]
  S --> V[Changed Keys / View Model]
  V --> P[Panel Invalidation]
  P --> D[Targeted DOM Patch]
  P --> C[Conditional Canvas Redraw]
```
