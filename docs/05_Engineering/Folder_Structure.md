# Folder Structure


> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented; requirements CI-enforced where automatable
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Current backend

```text
backend/
├─ brokers/      external broker/API integration
├─ storage/      caches/infrastructure
├─ oi/           option/OI/capital analytics
├─ analytics/    regime/FII-DII/smart-money analytics
├─ decision/     signals/confidence/strategy selection/executor
├─ risk/         account guard/reconciliation/risk meters
├─ strategy/     strategy definitions
├─ ml/           training/inference tooling
├─ backtest/     replay/snapshot logging
└─ tests/
```

This flat package layout is explicitly supported by current `pyproject.toml`;
a `src/` migration is not required merely for style.

## Current frontend

```text
frontend/
├─ Dashboard/
│  ├─ chain/
│  └─ components/
├─ PriceChart/
├─ engines/
├─ shared/
│  ├─ services/
│  ├─ state/
│  ├─ stores/
│  └─ utils/
└─ styles/
```

## Target ownership

- `shared/` only for genuinely cross-surface utilities/state/services.
- Domain calculations should migrate away from templates.
- Bootstrap files coordinate; they do not become feature dumping grounds.
- Styles are organized by tokens/base/layout/component/surface rather than repeated selectors.

Generated `frontend/dist/`, caches, databases, logs, `.env`, model artifacts
and runtime payloads are not editable source and remain ignored. Tests live
beside their runtime (`backend/tests`, `frontend/tools`, `frontend/e2e`), while
cross-cutting architecture documents remain under `docs/`.
