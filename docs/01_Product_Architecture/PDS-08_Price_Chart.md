# PDS-08 — Price Chart


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Mission

Answer:

> **What is price doing, and how does that price action relate to the current
> analytical decision?**

## Current implementation

Dedicated `frontend/PriceChart/` modules separate:
- data loading;
- chart rendering;
- indicators;
- interaction;
- history loading;
- standalone orchestration.

The Dashboard synchronizes rather than embedding the full engine.

## Contract

- Dedicated surface/tab.
- Receives synchronized symbol/context.
- Live ticks patch the current series.
- Historical loading is separate from live tick handling.
- User pan/zoom SHALL not be reset by normal ticks.
- Indicator calculations SHALL not block the live quote path.

## Dashboard relationship

Dashboard D-19 owns a link/sync path only. It does not own chart analytics.

## Acceptance

1. Opening chart from Dashboard preserves current symbol/context.
2. User zoom/pan remains stable during live ticks.
3. Historical fetch failure does not break current quote display.
4. Chart rendering remains isolated from Dashboard card rendering.
