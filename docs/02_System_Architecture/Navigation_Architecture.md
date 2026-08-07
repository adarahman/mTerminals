# Navigation Architecture


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Principle

Navigation follows analytical depth, not a menu of every report.

```text
Dashboard
  ├─ Option Chain
  │    └─ Strike Detail
  ├─ Capital Flow
  │    └─ Option Chain strike highlight
  ├─ Scenario Analysis
  ├─ Price Chart
  └─ Paper Trading
```

## Dashboard navigation

- Scroll is the default navigation between zones.
- Clickable card headers open their detail surface.
- Standalone `Open/View/Full Chain` buttons SHOULD not duplicate header navigation.
- Section mini-nav, if added, jumps only to zone boundaries.

## Context propagation

Navigation SHOULD preserve:
- symbol;
- expiry where relevant;
- selected strike when drilling down;
- source surface for back-navigation.

## Cross-report strike drill-down

A strike surfaced in Decision/Capital Flow may scroll to and highlight the same
strike in D-05 without opening another hierarchy.

## Back behavior

Closing a modal or detail view SHALL return to the invoking context and restore
focus when possible.
