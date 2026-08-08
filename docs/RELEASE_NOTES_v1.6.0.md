# mTerminals v1.6.0

Released: 2026-08-08

v1.6.0 closes the full architecture-conformance cycle across the implemented
application. Product specifications, system boundaries, UI rules, data
contracts, engineering standards, diagrams and the Dashboard audit now describe
and enforce the same shipped system.

## Highlights

- Versioned full/delta feed baselines reject incompatible updates and recover
  through a coherent snapshot.
- Dashboard state, navigation and modal events use canonical stores and compact
  semantic payloads.
- UI contracts now cover tokens, responsive behavior, tables, motion, modal
  focus, native controls and accessible chart descriptions.
- Snapshots publish a versioned data contract for row identity, units,
  nullability, provenance and freshness ownership.
- Capital and Greek exposure preserve missing-versus-zero semantics and prevent
  double lot-size conversion.
- Engineering CI now enforces focused Python correctness checks alongside all
  backend, frontend, build, browser, dependency and architecture gates.
- All nine Mermaid diagrams were rebuilt against the implemented architecture.
- The PDS-01 v1.2 audit closes all 22 historical findings with no open P0/P1
  contract violation and explicit triggers for two monitored optimizations.

## Verification baseline

- 205 backend tests.
- 5 critical browser journeys.
- 12 audit-evidence checks and 12 diagram checks.
- Complete frontend product, architecture, UI, data-model and engineering
  contract suites.
- Production frontend bundle build.
- Blocking npm and Python dependency audits in GitHub CI.

## Upgrade and operations

No runtime-data migration is required. Preserve `runtime/` and paper-trading
databases during deployment. Rebuild frontend assets, reinstall backend
dependencies, run deployment preflight, restart the service, then run the
health smoke check. Paper trading remains the default; live trading is not
enabled by this release.

See `docs/05_Engineering/Build_Deployment.md` and
`docs/05_Engineering/Operations_Runbook.md` for the full procedure.
