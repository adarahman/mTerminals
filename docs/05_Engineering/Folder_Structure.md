# Folder Structure


> **Product:** mTerminals
> **Architecture baseline:** post-v1.6.0 repository, reviewed 2026-08-31
> **Status:** Implemented; requirements CI-enforced where automatable
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Current backend

```text
src/
├─ main.py       process entry point
├─ server/       composition, HTTP/WebSocket transport, runtime services
├─ application/  use cases, pipeline coordination, payload assembly
├─ brokers/      external broker/API integration
├─ storage/      caches/infrastructure
├─ oi/           option/OI/capital analytics
├─ analytics/    regime/FII-DII/smart-money analytics
├─ decision/     signals/confidence/strategy selection/executor
├─ risk/         account guard/reconciliation/risk meters
├─ execution/    paper/live execution services
├─ strategy/     strategy definitions
├─ ml/           training/inference tooling
├─ backtest/     replay/snapshot logging
└─ tests/
```

The `src/` migration is complete. Runtime commands set `PYTHONPATH=src`, and
`src/main.py` delegates to the composition root in `src/server/app.py`.
References to `backend/` or root-level `ws_server_live.py` in
`docs/Existing_Project_Docs/` are preserved history, not current paths.

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
beside their runtime (`src/tests`, `frontend/tools`, `frontend/e2e`), while
cross-cutting architecture documents remain under `docs/`.
