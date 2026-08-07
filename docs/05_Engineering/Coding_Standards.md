# Coding Standards


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## General

- Prefer clear ownership over clever abstraction.
- Functions have one reason to change.
- Shared helpers are created only after genuine duplication is demonstrated.
- Public contracts use explicit names and documented units.

## Python

- Domain calculations should be deterministic where possible.
- External I/O belongs in adapters/services.
- Raise/return explicit errors rather than sentinel values that resemble valid data.
- Type hints SHOULD be added to stable domain boundaries.
- Tests cover decision/risk/capital units.

## JavaScript

- Keep bootstrap orchestration-only.
- Prefer pure view-model functions before DOM writes.
- Shared formatting in `formatters.js`.
- Shared state in stores, not duplicated globals.
- Avoid inline business formulas in HTML template strings.

## CSS

- Central semantic tokens.
- Avoid near-duplicate selectors across surface files.
- Component class names communicate role.
- Responsive overrides are predictable and minimal.

## Compatibility

Legacy `window.*` shims MAY remain during migration but new features SHOULD use
the canonical module/state path.
