# Event Architecture


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Purpose

Events communicate **things that happened**. State stores contain **what is true now**.

The project already has an `EventBus`; use it for semantic decoupling, not as a
second state store.

## Recommended event vocabulary

- `market:update`
- `feed:status`
- `symbol:change`
- `expiry:change`
- `range:change`
- `strike:select`
- `strike:highlight`
- `modal:open`
- `modal:close`
- `paper:portfolio-update`
- `decision:update`

## Rules

1. Event payloads SHOULD be small and typed/documented.
2. Consumers needing current data read canonical state after the event.
3. Events SHOULD not contain entire application snapshots unless transport-level.
4. A UI event SHALL not directly mutate domain analytics.
5. Event names describe semantics, not DOM selectors.

## Example

```text
expiry selector
   → app/view state update
   → expiry:change
   → data request/filter/update
   → MarketStore canonical state
   → affected panels refresh
```

## Implemented payload contracts

- `market:update`: `{ messageType, version }`; consumers read `MarketStore`.
- `decision:update`: `{ stateVersion }`; decision truth remains in market state.
- `feed:status`: the small `AppState.feedState` lifecycle object.
- `symbol:change`, `expiry:change`, `range:change`: changed identifier only.
- `strike:select`: `{ strike, source }`.
- `modal:open`, `modal:close`: `{ id }`.
- `paper:portfolio-update`: `{ source }`; account truth remains in market state.
