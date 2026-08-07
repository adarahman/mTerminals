# Error Handling


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
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
