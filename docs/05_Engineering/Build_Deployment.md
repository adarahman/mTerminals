# Build and Deployment


> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented; requirements CI-enforced where automatable
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Current frontend build

`npm run build` invokes:

```text
node build.mjs
node gen_html.mjs
```

using esbuild/PostCSS dependencies.

## Rules

- `dist/` is generated output and SHOULD not become a second editable source tree.
- Build input order for classic/global scripts SHALL remain explicit where order is required.
- A production build SHOULD fail on missing declared source assets.
- Bundle generation SHOULD preserve page-specific CSS/JS ownership.
- External GitHub Actions SHALL be pinned to a reviewed full commit SHA with
  the corresponding immutable release version retained as an inline comment.
- CI SHALL fail on high/critical npm advisories and known vulnerable Python
  packages. Lower-severity advisories SHALL be reviewed rather than hidden.
- Dependabot update volume SHALL remain bounded so automated maintenance does
  not crowd out product review.

## Backend packaging

Current `pyproject.toml` intentionally packages multiple top-level packages and
modules. Do not force a `src/` conversion without a concrete benefit.

## Operational health contract

`GET /health` is the canonical process and market-feed health endpoint:

```text
http://127.0.0.1:5500/health
```

Its top-level fields are:

| Field | Meaning |
|---|---|
| `status` | `ok` or `degraded` |
| `timestamp` | Server-local ISO-8601 observation time |
| `uptimeSeconds` | Current backend-process lifetime |
| `reasons` | Machine-readable list of current degradation reasons |
| `http.status` | HTTP listener health |
| `websocket.connectedClients` | Number of connected primary Dashboard clients |
| `marketFeed` | Session, symbol, expiry, snapshot age and SmartAPI state |

Market-feed states:

- `LIVE` — an open-market canonical payload is inside the freshness window;
- `STALE` — the market is open but the canonical payload is too old;
- `STARTING` — the market is open and no canonical payload exists yet;
- `IDLE` — the market is closed or on holiday, so live updates are not expected.

The endpoint returns HTTP `200` for `ok` and `503` for `degraded`. A closed
market with no new snapshot is `IDLE`, not a service failure. Operators SHALL
read the JSON body and `reasons`; a status code alone does not explain the
condition.

## Deployment verification

From the repository root, verify the exact candidate before restart:

```bash
git status --short
git rev-parse --short HEAD
.venv/bin/python backend/operational_readiness.py preflight

cd backend
../.venv/bin/python -m pytest

cd ../frontend
npm run test:health-status
npm run test:feed-recovery
npm run build
```

After restarting `ws_server_live.py`, verify:

```bash
.venv/bin/python backend/operational_readiness.py smoke
curl -i http://127.0.0.1:5500/health
curl -sS http://127.0.0.1:5500/metrics
```

Then smoke the production pages:

- `/dist/Dashboard/DashboardPro.html`;

Confirm the Dashboard feed badge agrees with `/health`, a forced WebSocket
disconnect recovers without a page reload, expiry switching returns a current
chain, Strike Detail returns to the Dashboard, and Paper Trading remains in
paper mode unless live trading was explicitly enabled.

See `Operations_Runbook.md` for backup, restore and incident response.

## Rollback

Rollback is a code operation, not a runtime-data restore. Preserve
`runtime/`, paper-trading data and caches unless a separate, reviewed data
recovery is required.

1. Stop `ws_server_live.py` so no process writes state during the switch.
2. Confirm the worktree is clean with `git status --short`.
3. Record the failed release commit with `git rev-parse HEAD`.
4. Switch to the last known-good release, for example
   `git switch --detach v1.0.0`.
5. Reinstall backend dependencies and rebuild the frontend from that tag.
6. Start the backend and repeat the health and page smoke checks above.
7. Return to current development later with `git switch main`.

Do not use `git reset --hard` as the normal rollback mechanism. If the
worktree is not clean, preserve or commit the changes before switching.

## Release checklist

1. Worktree is clean and the candidate commit is pushed.
2. Frontend contracts/build, backend pytest and Browser E2E are green in CI.
3. `/health` returns the expected `200 ok` response, or an explained `503`
   during a deliberately tested degraded open-market condition.
4. Dashboard visibly distinguishes `RECOVERING`, `STALE`, `PARTIAL` and
   `DISCONNECTED`, including a reason.
5. WebSocket reconnects and returns to `LIVE` after the next valid message.
6. Expiry change, Option Chain scrolling and Strike Detail handoff pass.
7. Dashboard-native OI Flow, Price Chart and Paper Trading smoke checks pass.
8. Runtime state is backed up when operational policy requires it.
9. `docs/CHANGELOG.md` describes the final candidate.
10. Create a new annotated semantic-version tag and push it; never move an
    already-published tag.
