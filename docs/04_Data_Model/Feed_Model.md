# Feed Model

> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

## Envelope and state

WebSocket full and delta envelopes carry `type`, `version`, and payload data.
Full snapshots establish a baseline; deltas identify that same baseline. The
canonical snapshot carries `symbol`, `expiry`, and `lastUpdated`. Status
messages are transport controls, not market snapshots.

Client states are connecting, live, partial, stale, disconnected and
recovering. The shared market store owns state; individual screens consume it.

## Delta and freshness rules

A client SHALL reject a delta whose version does not match its active full
baseline and reconnect for recovery. Symbol or expiry changes invalidate the
old baseline. Arrays replace by default; keyed chain merging is allowed only
when full `symbol + expiry + strike` identity remains intact.

`lastUpdated` is the observation/export timestamp. Freshness tolerance is a
domain setting. Consumers SHALL NOT infer freshness from an animated UI
indicator. Missing snapshots expose degraded state and never invented values.
Unknown envelope types and incompatible versions are ignored and recovered.

**Owner:** backend exporter and WebSocket transport. **Approved consumer:**
shared market store, which fans canonical state out to screens.
