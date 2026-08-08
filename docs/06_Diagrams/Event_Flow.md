# Event Flow

> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

```mermaid
sequenceDiagram
  actor User
  participant UI as UI control
  participant State as AppState
  participant Bus as EventBus
  participant Data as DataService / MarketStore
  participant View as Panels
  User->>UI: choose symbol or expiry
  UI->>State: update selected context
  UI->>Bus: symbol:change or expiry:change
  UI->>Data: reconnect with requested context
  Data-->>Data: require coherent full baseline
  Data->>State: commit canonical snapshot
  Data->>Bus: market:update metadata
  Bus->>View: invalidate affected presentation
  View-->>User: targeted patch with context preserved
```

Navigation and modal events carry identifiers only. Market snapshots stay in
the canonical store rather than event payloads.
