# Browser end-to-end tests

These tests exercise the production frontend served by the running
`ws_server_live.py` process.

1. Build the frontend after source changes with `npm run build`.
2. Run `npm run test:e2e` from `frontend/`.

The suite reuses the live backend when port 5500 is already available. If it
is not, Playwright starts a local static server automatically. The Option
Chain handoff test injects a deterministic chain snapshot in the browser, so
CI never needs broker credentials or an external market-data connection.

Environment overrides:

- `MTERMINALS_E2E_BASE_URL` changes the Dashboard URL.
- `MTERMINALS_E2E_CHANNEL` changes the installed Playwright browser channel;
  the local default is `chrome`.

Failure traces and screenshots are written under ignored Playwright artifact
directories. Video is intentionally disabled so the suite does not require
Playwright's optional FFmpeg download.
