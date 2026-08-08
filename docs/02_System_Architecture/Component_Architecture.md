# Component Architecture


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Frontend component responsibilities

### Bootstrap
`Dashboard/dashboard.js` SHOULD remain orchestration-only.

### State/services
- `shared/services/ws-manager.js` — transport lifecycle.
- `shared/stores/market-store.js` — canonical live market state.
- `shared/state/app-state.js` — application/view context.
- `shared/utils/event-bus.js` — semantic events, not data storage.

### Panel lifecycle
`panel-manager.js` and `dashboard-panels.js` define panel lifecycle boundaries.

### Chain modules
`Dashboard/chain/*` separate:
- helpers;
- templates;
- view-models;
- rendering;
- Greeks;
- synchronization.

This separation SHOULD continue until business logic can be tested independently
from DOM string generation.

## Component rule

A component owns:
- its DOM subtree;
- its interaction state;
- its formatting.

It does not own:
- shared analytical truth;
- another component's DOM;
- transport connection.

## Desired card interface

Conceptually each live component should be able to support:

```text
init(context)
update(changes, state)
resize()
destroy()
```

without requiring a whole-page rerender.

## HTML builder boundary

Pure builders/view-models are acceptable. DOM-writing functions SHALL be isolated
so they can be profiled and progressively replaced with targeted patches.

## Implementation status

Implemented through `PanelManager`, lifecycle-isolated panel wrappers, dedicated
chain helper/template/view-model/rendering modules, and a bootstrap-only
`dashboard.js`. DOM mutation helpers are centralized and architecture-tested.
