# Architecture Changelog

## 2026-08-08 — mTerminals v1.6.0 architecture conformance

### Audit closure

- Preserved the 2026-08-07 PDS-01 audit as an explicitly superseded historical
  record and published a current PDS-01 v1.3 compliance audit.
- Traced and dispositioned all 22 historical P0/P1/P2 findings against current
  source, leaving no open P0/P1 contract violation.
- Added CI checks tying audit closure claims to implementation evidence and
  documented triggers for the two residual performance optimizations.

### Diagram conformance

- Rebuilt all nine architecture diagrams against the implemented v1.5 system.
- Added coherent baseline recovery, session overlays, execution gates, complete
  D-00–D-19 layout, context restoration and live/scenario separation.
- Added CI checks for diagram presence, Mermaid fencing and critical
  architecture relationships.

### Engineering conformance

- Completed all nine engineering standards against the shipped repository and
  current v1.5.0 release line.
- Added a focused Python correctness lint gate without forcing a broad legacy
  formatting rewrite.
- Added CI-enforced engineering contracts covering builds, tests, operations,
  logging, ignored artifacts, supply-chain checks and version guidance.

### Data-model conformance

- Published a versioned, machine-readable payload contract for identity, units,
  nullability, provenance and freshness ownership.
- Completed the feed, option, Greeks, capital, institutional, decision and
  derived-metric model specifications against the shipped implementation.
- Added CI enforcement for all seven data-model contracts and critical unit,
  baseline, missing-value and decision-provenance invariants.

### UI system conformance

- Replaced remaining loaded clickable text controls with labeled native buttons.
- Added accessible questions, units and fallback descriptions to every shipped canvas.
- Added CI-enforced UI-system contracts for color, typography, layout, tables,
  motion, modals, responsive behavior and documentation status.
- Recorded implementation status across the complete UI-system package.

### System architecture conformance

- Added versioned full/delta baselines with automatic coherent recovery.
- Converted semantic market/navigation/modal events to compact payloads.
- Added CI-enforced backend dependency direction and frontend architecture contracts.
- Documented canonical unit boundaries and implementation status across the
  complete system-architecture package.

## 2026-08-08 — mTerminals v1.5.0 architecture release

- Published the complete architecture package baseline and the corrected
  Dashboard implementation audit.
- Improved Strike Detail context and dedicated Option Chain handoff.

## 2026-08-08 — mTerminals v1.4.1 OI-history performance fix

- Bounded live OI-velocity history to 35 minutes, removed duplicate per-tick
  appends, and preserved the former multi-million-row file as a legacy archive.

## 2026-08-08 — mTerminals v1.4.0 operational readiness

- Added a fail-fast deployment preflight for required broker settings, runtime
  storage writability, HTTP port availability and live-trading visibility.
- Added a repeatable post-start health smoke command.
- Added tested backup, checksum, restore and incident-response procedures.

## 2026-08-08 — mTerminals v1.3.0 supply-chain hardening

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
