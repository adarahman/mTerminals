# Event Flow


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


```mermaid
sequenceDiagram
  participant U as User
  participant A as AppState
  participant E as EventBus
  participant S as Data/Store
  participant P as Panels
  U->>A: change expiry
  A->>E: expiry:change
  E->>S: request/apply context
  S->>P: canonical update
  P->>P: targeted patch
```
