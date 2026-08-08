# Architecture Changelog

## Unreleased — mTerminals v1.3.0 supply-chain hardening

- Pinned official GitHub Actions to immutable release commit SHAs and upgraded
  them to Node 24-compatible major releases.
- Added blocking high-severity npm and Python vulnerability audit gates.
- Raised vulnerable `aiohttp` and build-tool minimum versions.
- Added bounded weekly Dependabot updates for Actions, npm and Python.
- Added a contract test preventing floating Action tags or missing audit gates.

## 2026-08-08 — mTerminals v1.2.0 observability

- Added default JSON backend logs with an explicit operational field allowlist.
- Expanded credential redaction across standard logging and SmartAPI/logzero
  handlers.
- Added structured WebSocket lifecycle and deduplicated health-transition
  events without logging high-frequency market ticks.
- Added deterministic tests for credential redaction, JSON formatting and
  transition deduplication.
- Added a payload-free `/metrics` endpoint for WebSocket, reconnect, pipeline,
  stale-feed, recovery and process-uptime telemetry.

## 2026-08-08 — mTerminals v1.1.0 production hardening

- Added `GET /health` as the operational health contract for the HTTP
  service, connected Dashboard WebSocket clients, market session, canonical
  payload freshness and SmartAPI overlay state.
- Added explicit Dashboard feed states and reasons for connecting,
  recovering, partial, stale and disconnected operation.
- Added deterministic WebSocket recovery coverage for disconnect,
  reconnect, stale detection and recovery to live data.
- Fixed obsolete reconnect timers replacing a newer healthy socket.
- Added CI gates for health/feed-status and reconnect/recovery contracts.
- Added deployment verification, rollback guidance and a v1.1.0 release
  checklist to the engineering documentation.

## 2026-08-07 — Complete Architecture Package 1.0

- Added master index and package guide.
- Added PDS-00 through PDS-08.
- Included completed Dashboard PDS-01 v1.1.
- Added system, information, navigation, component, rendering, event, state,
  data-flow, dependency and metric-ownership architecture.
- Added UI standards.
- Added data model contracts.
- Added engineering standards.
- Added Mermaid architecture diagrams.
- Aligned package to current mTerminals frontend/backend project structure.
- Explicitly documented current-to-target rendering migration rather than
  claiming the current outerHTML hot path is already field-level.
