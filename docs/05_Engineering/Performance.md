# Performance


> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented; requirements CI-enforced where automatable
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Critical path

WebSocket ingest → canonical state → Decision/Status patch is highest priority.

## Frontend

- Avoid full-page re-render.
- Avoid rebuilding unchanged cards.
- Batch rapid updates.
- Preserve scroll/focus.
- Invalidate charts by relevant keys.
- Profile `_rerenderChainPanels` / equivalent hot paths before broad rewrites.

## Backend

- Cache external source calls appropriately.
- Reuse normalized master tables for related metrics.
- Do not recalculate identical capital/chain metrics independently for every consumer.
- Slow optional analytics SHOULD fail/degrade without blocking core feed.

## Measurement

Performance work SHOULD record:
- tick frequency;
- ingest time;
- derived metric time;
- render time;
- long tasks;
- chart redraw frequency.

Optimize measured bottlenecks.

## Current instrumentation and budgets

`GET /metrics` owns process uptime, pipeline run/failure counts, latest
pipeline duration, feed transitions and recovery counters. Frontend hot paths
avoid unchanged writes and preserve interactive subtrees. Performance changes
SHALL report before/after measurements; no universal millisecond budget is
asserted until representative production traces exist. Live history SHALL be
bounded; OI velocity retains its domain window rather than an unbounded file.
