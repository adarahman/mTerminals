# Feed State Diagram


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


```mermaid
stateDiagram-v2
  [*] --> Connecting
  Connecting --> Live
  Live --> Partial
  Live --> Stale
  Stale --> Disconnected
  Disconnected --> Recovering
  Recovering --> Live
  Partial --> Live
```
