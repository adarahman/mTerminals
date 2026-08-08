# Error Handling


> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented; requirements CI-enforced where automatable
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Philosophy

Preserve valid information, isolate failure, make freshness explicit.

## UI

- No `NaN`, `undefined`, traceback or raw exception text in normal panels.
- Stale last-valid data may remain visible with a stale marker.
- Failed secondary analytics do not blank Decision/Status if valid core state exists.
- Loading and zero are distinct.

## Backend

Errors SHOULD include:
- subsystem;
- operation;
- symbol/expiry context where safe;
- retryability;
- original exception chain in logs.

## Recovery

Reconnect requires coherent baseline before `LIVE`.
Repeated failures should use controlled retry/backoff rather than tight loops.

## Boundary contract

Expected domain absence uses nullable values or a typed degraded result. Truly
exceptional failures retain their exception chain in structured logs. HTTP and
WebSocket boundaries return safe summaries; raw exception text, credentials and
broker responses SHALL NOT enter normal UI panels. A secondary analytics
failure may omit or degrade that block, but execution gates fail closed when
required decision or risk evidence is missing.
