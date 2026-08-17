# Operations Runbook

> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented; requirements CI-enforced where automatable
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.

## Before starting production

Run `.venv/bin/python backend/operational_readiness.py preflight` before every
restart. For an intentional REST-only start, add `--no-broker`. The command
fails if required credentials are absent, runtime storage is not writable, or
the HTTP port is occupied. It warns when live trading is enabled.

## After starting production

Run `.venv/bin/python backend/operational_readiness.py smoke`. This requires an
HTTP 200 response with `status: ok`. During a feed incident, inspect `/health`
reasons and confirm the Dashboard feed state agrees with that endpoint.

## Runtime backup

`runtime/` contains accumulated caches and history; preserve it when continuity
matters. Paper-trading SQLite files are also operational state. Credentials,
`.env`, logs, generated frontend files and the live-trading kill-switch are not
backup artifacts.

1. Stop the backend so SQLite and history writers are quiescent.
2. Record the current commit and UTC timestamp.
3. Copy `runtime/` and paper-trading databases into a timestamped archive on
   storage outside the repository.
4. Generate and retain a SHA-256 checksum for the archive.
5. Restart the backend and run the smoke command.

## Restore

1. Stop the backend and preserve the current failed state separately.
2. Verify the archive checksum before extraction.
3. Restore into a temporary directory, inspect ownership and permissions, then
   move the validated state into the configured `RUNTIME_DIR`.
4. Never restore `.env` or credentials from a runtime archive.
5. Start the same application release that created the backup when practical.
6. Run preflight, start the service, run smoke, and inspect the Dashboard,
   Option Chain and Paper Trading state.

## Incident triage

1. Capture the release commit, UTC time, `/health`, `/metrics`, and relevant
   structured logs. Do not capture market payloads or credentials.
2. If live trading may be unsafe, create `LIVE_TRADING_KILL` immediately and
   verify orders are rejected.
3. Distinguish process failure, stale upstream feed, broker authentication,
   storage exhaustion and frontend connectivity before changing code.
4. Roll back code using a tagged release; restore data only with evidence of
   state corruption or loss.
5. After recovery, verify feed freshness, reconnection, expiry switching,
   Strike Detail handoff and paper/live mode before closing the incident.

## Escalation and evidence

Record an incident owner, start/end time, affected release, user-visible impact,
mitigation and follow-up. Preserve only bounded structured logs and health
snapshots needed for diagnosis. Never attach `.env`, authorization headers,
complete market payloads or account responses. Recovery is complete only after
the health endpoint, Dashboard state and order-safety mode agree.
