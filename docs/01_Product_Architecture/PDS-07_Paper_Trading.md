# PDS-07 — Paper Trading


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Mission

Provide a safe simulation surface for order entry, portfolio tracking and
strategy evaluation without implying that simulated fills equal real fills.

## Current implementation

Current code includes:
- `backend/paper_trading.py`;
- Dashboard paper-trading UI modules;
- order-entry, portfolio-tracker and shared paper-trading utilities.

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
