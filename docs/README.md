# mTerminals — Professional Architecture Package


> **Product:** mTerminals
> **Architecture baseline:** mTerminals v1.6.0
> **Status:** Implementation-aligned and CI-enforced
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


This directory is the architecture and product-design source of truth for the
implementation in the same repository. It records stable product rules and the
evidence that keeps JavaScript, Python, build tooling and broker integrations
aligned with them.

## How to use this package

1. Start with `00_MASTER_INDEX.md`.
2. Product/UI work begins with `01_Product_Architecture/PDS-00_Global_Design_System.md`.
   `DESIGN_SYSTEM.md` preserves the broader product and component blueprint.
3. Dashboard work must comply with `PDS-01_Dashboard.md`.
4. Engineering refactors should read `02_System_Architecture/System_Architecture.md`
   and `05_Engineering/Folder_Structure.md` together.
5. Any shared metric change must be checked against
   `02_System_Architecture/Metric_Ownership.md`.
6. Any live-rendering change must be checked against
   `02_System_Architecture/Rendering_Architecture.md`.
7. Any release that changes a contract SHALL update `CHANGELOG.md`.

## Current implementation baseline

The package is aligned to the observed project structure:

- Backend: layered packages under `src/`, including `application/`, `server/`,
  `brokers/`, `decision/`, `oi/`, `analytics/`, `risk/`, and `storage/`.
- Frontend: `Dashboard/`, `OptionChain/`, `OIFlow/`, `PriceChart/`,
  `shared/state`, `shared/stores`, `shared/services`, `shared/utils`.
- Existing infrastructure: `MarketStore`, `WSManager`, `PanelManager`,
  event bus, targeted DOM utilities, dashboard chain renderer, build scripts.
- Build: `frontend/build.mjs` + `frontend/gen_html.mjs`.
- Process entry point: `python -m main`; composition root: `server/app.py`.

The v1.6.0 package is implementation-aligned. Any remaining target language is
an explicitly identified optimization or migration, not an unqualified claim
about current behavior.

## Completion and enforcement

- PDS-00 through PDS-08 are implemented and regression-covered.
- System, UI, data-model and engineering packages record implementation status.
- All nine architecture diagrams are CI-checked.
- The current Dashboard audit closes all historical P0/P1 findings.
- GitHub CI runs backend tests, frontend contract gates, production builds,
  Browser E2E and dependency audits.

Release notes: `RELEASE_NOTES_v1.6.0.md`.

## Existing project documents

The following historical files are preserved from the original project snapshot:

- `Existing_Project_Docs/CHANGELOG.md`
- `Existing_Project_Docs/PROJECT-ARCHITECTURE.md`

The documentation package's own revision history is stored separately as:

- `ARCHITECTURE_CHANGELOG.md`

This separation prevents the architecture-documentation history from overwriting or being confused with the application's existing project history.
