# Logging


> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented; requirements CI-enforced where automatable
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

## Runtime format

Backend logging defaults to one JSON object per line. Each structured record
contains `timestamp`, `level`, `logger` and `message`; operational records MAY
also contain the allowlisted fields `event`, `subsystem`, `status`, `reason`,
`symbol`, `expiry`, `connected_clients`, `age_seconds` and
`duration_seconds`.

Set `LOG_FORMAT=text` only for temporary local readability. Set `LOG_LEVEL`
to the required standard logging level. Production and captured diagnostic
logs SHOULD retain the default JSON format.

## Redaction

Redaction is attached to logging handlers, including the SmartAPI SDK's
`logzero` handlers, so propagated third-party records are filtered before
output. It covers authorization/private-key headers and common credential
keys including API keys, access/feed/JWT tokens, client codes, TOTP secrets,
passwords and PINs. Structured logging only includes an explicit allowlist of
extra fields; arbitrary record payloads are not serialized.

Redaction tests SHALL accompany changes to authentication fields or broker SDK
logging. Logs remain sensitive operational data even after filtering and
SHALL follow normal access and retention controls.

## Operational events

The server emits structured, low-frequency lifecycle events including:

- `websocket.connected` and `websocket.disconnected`;
- `health.transition` when service/feed health changes.

Repeated health polls in the same state do not create duplicate transition
records. Per-tick market data is not written to operational logs.

## Operational metrics

`GET /metrics` returns a bounded JSON snapshot with process-local counters
and gauges. Counters cover WebSocket connections, automatic reconnects,
disconnects, pipeline runs/failures, health transitions, stale events and
recoveries. Gauges cover connected clients, latest pipeline duration,
last pipeline success/failure timestamps and process uptime.

The endpoint intentionally has no market-data labels or payloads: no chain,
symbol, order, position, account or credential data is exposed. Values reset
when the backend process restarts; this endpoint is for lightweight local
monitoring, not durable accounting or trading audit history.
