# Testing Strategy


> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented; requirements CI-enforced where automatable
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Existing strength

Current backend tests already cover risk, decision, SmartAPI, paper trading,
reconciliation, backtest and strategy paths.

## Required layers

### Unit
- chain metrics;
- capital units;
- confidence/signals;
- risk rules;
- view-model pure functions.

### Integration
- feed adapter → normalized state;
- full + delta merge;
- decision with degraded inputs;
- paper order lifecycle.

### Frontend behavior
- expiry synchronization;
- modal keyboard close;
- D-05 scroll preservation;
- no conflicting shared metrics;
- responsive order;
- WebSocket disconnect and single delayed reconnect;
- `RECOVERING` until the first coherent message;
- `LIVE` → `STALE` → `LIVE` feed recovery;
- shared feed-state recovery propagation to Option Chain.

### Regression fixtures
Maintain representative snapshots for:
- normal live market;
- missing Greeks;
- partial chain;
- stale feed;
- expiry change;
- extreme OI/capital concentration.

## Release gate

No architecture-critical behavior is considered complete without regression coverage
or a documented manual verification checklist.

Current local release commands include:

```bash
cd backend && ../.venv/bin/python -m pytest
../.venv/bin/ruff check . --select E9,F63,F7,F82
cd ../frontend && npm run test:engineering
npm run build
npm run test:e2e
```

GitHub Actions is authoritative for Browser E2E when the local execution
environment cannot bind the temporary static-server port.

CI additionally executes every named frontend contract suite, dependency
audits, the full backend suite and browser journeys. Tests requiring network,
credentials or a live market SHALL be separated from deterministic release
gates and documented as manual smoke checks.
