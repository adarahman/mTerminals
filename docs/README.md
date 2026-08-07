# mTerminals — Professional Architecture Package


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


This repository is the architecture and product-design source of truth for mTerminals.
It is intentionally separated from implementation code. It records the product rules
that should remain stable even while JavaScript, Python modules, build tooling, or
broker integrations change.

## How to use this package

1. Start with `00_MASTER_INDEX.md`.
2. Product/UI work begins with `01_Product_Architecture/PDS-00_Global_Design_System.md`.
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

- Backend: `decision/`, `oi/`, `analytics/`, `risk/`, `brokers/`, `storage/`,
  `strategy/`, `ml/`, plus orchestration modules such as `engine.py`.
- Frontend: `Dashboard/`, `OptionChain/`, `OIFlow/`, `PriceChart/`,
  `shared/state`, `shared/stores`, `shared/services`, `shared/utils`.
- Existing infrastructure: `MarketStore`, `WSManager`, `PanelManager`,
  event bus, targeted DOM utilities, dashboard chain renderer, build scripts.
- Build: `frontend/build.mjs` + `frontend/gen_html.mjs`.
- Live transport: `ws_server_live.py`.

This documentation distinguishes **CURRENT** implementation facts from **TARGET**
architecture rules where a migration is still needed.

## Existing project documents

The following files are preserved from the existing mTerminals project snapshot and are kept **unchanged**:

- `Existing_Project_Docs/CHANGELOG.md`
- `Existing_Project_Docs/PROJECT-ARCHITECTURE.md`

The documentation package's own revision history is stored separately as:

- `ARCHITECTURE_CHANGELOG.md`

This separation prevents the architecture-documentation history from overwriting or being confused with the application's existing project history.

