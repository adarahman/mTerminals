# Logging


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Goals

Logs should answer:
- what failed;
- where;
- for which symbol/expiry/order;
- whether recovery occurred.

## Levels

- DEBUG: development diagnostics.
- INFO: lifecycle milestones.
- WARNING: degraded/retryable state.
- ERROR: operation failure.
- CRITICAL: unsafe/unrecoverable trading state.

## Rules

Do not log credentials, tokens, passwords or complete sensitive configuration.
High-frequency tick logging SHOULD be sampled/aggregated to avoid becoming the bottleneck.
