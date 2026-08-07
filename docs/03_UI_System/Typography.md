# Typography


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Roles

Use semantic roles rather than one-off font sizes:
Page Title, Zone Title, Card Title, Metric Label, Metric Value, Display Numeric,
Body, Caption, Table Numeric.

## Trading-specific requirements

- Numeric fields SHOULD use tabular figures.
- Decimal precision is metric-specific and centralized in formatters.
- Units SHALL not be hidden when ambiguity is possible.
- Positive/negative sign formatting SHALL be consistent.
- Dense tables prioritize legibility over decorative typography.

## Hierarchy

The largest text on Dashboard SHOULD be decision/spot-level information, not section decoration.
