# Modal Standard


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


Every modal:
- has title and close control;
- supports `Esc`;
- supports safe backdrop close;
- traps and restores focus;
- survives live ticks;
- does not use full-page navigation for simple Tier-3 inspection.

Large modal content SHOULD own its internal scrolling rather than growing beyond viewport.

## Implementation status

Shared modal management supplies dialog semantics, labeled close controls,
Escape and safe backdrop closure, focus trapping/restoration, single-modal
ownership and viewport-bounded internal scrolling. End-to-end tests cover the
shared contract and live cross-surface Strike Detail handoff.
