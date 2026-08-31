# PDS-07 — Paper Trading


> **Product:** mTerminals  
> **Architecture baseline:** post-v1.6.0 repository, reviewed 2026-08-31
> **Status:** Implemented contract; requirements remain authoritative
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Mission

Provide a safe simulation surface for order entry, portfolio tracking and
strategy evaluation without implying that simulated fills equal real fills.

## Current implementation

Current code includes:
- `src/execution/paper_trading.py` for the simulation engine;
- `src/server/paper_portfolio.py` for server-side portfolio transport;
- `frontend/Dashboard/order-entry.js`;
- `frontend/Dashboard/portfolio-tracker.js`;
- `frontend/Dashboard/paper-trading-shared.js`.

## Required concepts

- account/fund summary;
- open positions;
- realized/unrealized P&L;
- order history;
- simulated fills;
- strategy association where available.

## Separation from analytics

Paper Trading consumes market prices but SHALL NOT become a source of market
analytics. Account state and market state are separate stores/concepts.

## Order workflow

```text
Draft → Validate → Submit Simulation → Filled/Rejected → Position → Close
```

Each transition SHALL be explicit and testable.

## Fill transparency

Simulation assumptions (price source, slippage, delay, lot quantity) SHOULD be
documented. A paper fill SHALL not be represented as exchange confirmation.

## Safety

Any UI shared with live trading SHALL visually distinguish simulation from live
execution. Live-trading kill/guard mechanisms remain outside this PDS but must
not be bypassed.

## Acceptance

1. Reload/reconnect does not duplicate submitted paper orders.
2. Fund summary reconciles with open/closed simulated trades.
3. Missing live price does not create a fabricated fill.
4. Paper state cannot alter Decision Engine inputs.
