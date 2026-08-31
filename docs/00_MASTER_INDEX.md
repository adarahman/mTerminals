# mTerminals Architecture — Master Index


> **Product:** mTerminals
> **Architecture baseline:** post-v1.6.0 repository, reviewed 2026-08-31
> **Status:** Current navigation and authority map; contracts are CI-enforced
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Product architecture

`DESIGN_SYSTEM.md` contains the broader product vision and component blueprint;
PDS-00 remains authoritative for implemented global UI requirements.

| ID | Document | Purpose |
|---|---|---|
| PDS-00 | Global Design System | Shared visual/interaction rules |
| PDS-01 | Dashboard | Main decision surface |
| PDS-02 | Option Chain | Dense strike-analysis surface |
| PDS-03 | Strike Detail Report | Single-strike investigation |
| PDS-04 | Capital Flow | Capital and OI flow interpretation |
| PDS-05 | Decision Engine | Decision semantics and UI contract |
| PDS-06 | Scenario Analysis | Scenario-adjusted analysis |
| PDS-07 | Paper Trading | Simulation account and order workflow |
| PDS-08 | Price Chart | Dedicated price-action surface |

## System architecture

System architecture, information architecture, navigation, component boundaries,
rendering, event model, state management, data flow, dependency rules and metric ownership.

## UI system

Typography, semantic colors, layout, cards, charts, tables, modals, motion,
accessibility and responsive behavior.

## Data model

Feed, option-chain, Greeks, capital, institutional, decision and derived-metric contracts.

## Engineering

Folder structure, coding standards, performance, error handling, logging, testing,
versioning, build and deployment.

## Diagrams

The `06_Diagrams/` directory contains Mermaid diagrams that can render directly in
GitHub and many Markdown editors.

## Audits

`07_Audits/` preserves the 2026-08-07 PDS-01 audit and its v1.2 closure audit.
Both are dated release evidence rather than a description of every later
repository change. The historical v1.1 file is explicitly superseded; use the
active architecture and engineering documents for the current repository.

## Release and operations

- `RELEASE_NOTES_v1.6.0.md` — release scope and verification baseline.
- `CHANGELOG.md` — application/architecture change history.
- `05_Engineering/Build_Deployment.md` — build, health and rollback procedure.
- `05_Engineering/Operations_Runbook.md` — preflight, smoke, backup and incident response.

## Conflict precedence

When documents conflict, precedence is:

1. Explicit newer PDS revision for the affected product surface.
2. Global PDS-00 for shared UI behavior.
3. Metric Ownership for shared metric semantics.
4. System Architecture for technical boundaries.
5. Engineering standards for implementation conventions.

## Preserved existing project documentation

- `Existing_Project_Docs/CHANGELOG.md` — original mTerminals project changelog;
  historical paths such as `backend/` and `ws_server_live.py` are intentional.
- `Existing_Project_Docs/PROJECT-ARCHITECTURE.md` — original project architecture
  snapshot, not valid guidance for the current `src/` layout.
- `ARCHITECTURE_CHANGELOG.md` — changelog for this new professional architecture package.
