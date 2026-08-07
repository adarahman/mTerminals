# Layout System


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Principles

- Stable grid.
- Predictable gutters.
- Zone separation.
- Full-width Decision Engine.
- Dense tables contain their own overflow.
- Responsive stacking preserves reading order.

## Dashboard desktop

3-up Structure row, 2-up Capital row, 3-up Institutional row plus full-width D-12.

## Compact

Multi-column groups become single column in source order.

## Rule

A card's content growth SHALL not unexpectedly reflow unrelated zones when that
content belongs in Tier 3.
