# Responsive Design


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Breakpoints

- Desktop: >=1280px.
- Compact: <1280px.
- Phone-specific layout: future PDS.

## Rules

- Preserve information and order.
- Collapse grids, not semantics.
- Tables manage local overflow.
- Modal content fits viewport.
- No card disappears solely because of Compact width.

## Implementation status

The `<1280px` compact breakpoint collapses grids without hiding cards or changing
source order. Navigation remains reachable, tables scroll locally, and modal
content is viewport bounded. Browser coverage verifies a 1024px viewport has no
page-level horizontal overflow.
