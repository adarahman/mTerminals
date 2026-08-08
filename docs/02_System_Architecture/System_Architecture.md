# System Architecture


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## 1. Architectural objective

mTerminals SHOULD converge on a layered, event-driven trading application where
data acquisition, analytics, decisioning, risk, state and presentation are
separable and independently testable.

## 2. Current high-level shape

```text
Broker/NSE Sources
      ↓
Python backend + ws_server_live.py
      ↓
JSON / WebSocket
      ↓
WSManager → MarketStore
      ↓
Dashboard / OptionChain / PriceChart / Paper Trading
```

Backend packages already provide useful separation:
`brokers → storage/infrastructure → oi/analytics → decision → risk/execution`.

Frontend already contains early infrastructure:
`ws-manager.js`, `market-store.js`, `app-state.js`, `event-bus.js`,
`panel-manager.js`, DOM patch utilities.

## 3. Target layers

### Layer A — Acquisition
`brokers/`, `market_api.py`, SmartAPI adapters, NSE data fetchers.

Responsibilities:
- external I/O;
- authentication/session;
- normalization of source peculiarities;
- reconnect/retry.

### Layer B — Storage/cache
`storage/`, runtime cache.

Responsibilities:
- TTL cache;
- snapshot persistence;
- no trading-domain decisions.

### Layer C — Domain analytics
`oi/`, `analytics/`, selected market-structure functions.

Responsibilities:
- deterministic calculations;
- explicit units;
- no UI formatting.

### Layer D — Decision
`decision/`, `strategy/`.

Responsibilities:
- signal aggregation;
- confidence;
- strategy selection.

### Layer E — Risk/execution
`risk/`, execution adapters, auto executor, live kill controls.

Responsibilities:
- eligibility;
- reconciliation;
- account guard;
- execution safety.

### Layer F — Transport/state
WebSocket payload, frontend `WSManager`, `MarketStore`.

Responsibilities:
- full/delta state delivery;
- canonical client state;
- version/freshness.

### Layer G — Presentation
Dashboard/OptionChain/OIFlow/PriceChart.

Responsibilities:
- visualization;
- interaction state;
- no duplicated domain metric calculation.

## 4. Dependency direction

Dependencies SHOULD move downward toward lower-level data/infrastructure,
not sideways through UI components.

Prohibited examples:
- presentation importing risk internals to compute a metric;
- storage importing decision;
- a UI card scraping another card's DOM;
- broker adapter importing strategy.

## 5. Current-to-target migration

### CURRENT
Some Dashboard chain rendering still rebuilds full outerHTML strings per tick.

### TARGET
Canonical store change detection → component-specific field/row patches.

### CURRENT
Event bus exists but is lightly/not yet used.

### TARGET
Use events for semantic application actions (`symbol:change`,
`expiry:change`, `market:update`) without replacing canonical state.

### CURRENT
PanelManager provides lifecycle wrappers.

### TARGET
Make panel ownership explicit and keep bootstrap files orchestration-only.

## 6. Non-goals

This architecture does not require a React migration, microservices, Redux,
or a backend framework rewrite. Improvements should follow demonstrated
complexity/performance needs, not fashion.

## 7. Core invariants

1. Analytics do not depend on DOM.
2. Risk does not depend on UI.
3. UI does not independently derive canonical shared metrics.
4. Transport messages are validated before becoming canonical state.
5. A reconnect cannot silently mix incompatible snapshot/delta versions.
6. A single failed analytical module degrades locally where possible.

## 8. Implementation status (v1.5 line)

- Layers A–G have explicit package/component owners.
- Full snapshots establish a versioned client baseline; incompatible deltas
  are rejected and trigger coherent recovery.
- Semantic events carry identifiers/version metadata rather than duplicating
  canonical market snapshots.
- Panel failures are isolated by `PanelManager` lifecycle guards.
- High-frequency top-bar/ticker updates patch stable DOM nodes in place;
  structural rebuilds are reserved for structural changes.
- CI enforces frontend architecture contracts and backend dependency direction.
