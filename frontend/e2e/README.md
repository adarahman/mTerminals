# Browser end-to-end tests

These tests exercise the production frontend served by the running
`ws_server_live.py` process.

1. Start the backend normally and confirm the Dashboard is available at
   `http://127.0.0.1:5500/dist/Dashboard/DashboardPro.html`.
2. Build the frontend after source changes with `npm run build`.
3. Run `npm run test:e2e` from `frontend/`.

Environment overrides:

- `MTERMINALS_E2E_BASE_URL` changes the Dashboard URL.
- `MTERMINALS_E2E_CHANNEL` changes the installed Playwright browser channel;
  the local default is `chrome`.

Failure traces and screenshots are written under ignored Playwright artifact
directories. Video is intentionally disabled so the suite does not require
Playwright's optional FFmpeg download. The Option Chain handoff test requires
the running backend to provide at least one live or last-known chain row.
