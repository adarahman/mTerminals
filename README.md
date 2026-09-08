# mTerminals

mTerminals is a live F&O decision dashboard for Indian index derivatives. It
combines option-chain, OI, Greeks, capital-flow, institutional and volatility
analytics with a provenance-aware decision engine, scenario analysis, guarded
paper/live execution, and dedicated Option Chain and Price Chart surfaces.

Current repository release: **v1.6.0**.

## Start here

- [Architecture master index](docs/00_MASTER_INDEX.md)
- [Product architecture](docs/01_Product_Architecture)
- [System architecture](docs/02_System_Architecture)
- [UI system](docs/03_UI_System)
- [Data model](docs/04_Data_Model)
- [Engineering and operations](docs/05_Engineering)
- [Architecture diagrams](docs/06_Diagrams)
- [Current implementation audit](docs/07_Audits/DASHBOARD_PDS01_IMPLEMENTATION_AUDIT_v1.2.md)
- [v1.6.0 release notes](docs/RELEASE_NOTES_v1.6.0.md)

## Local setup

Backend requirements are Python 3.10+; frontend tooling uses Node.js 22.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e 'src[dev]'
cd frontend && npm ci && npm run build
```

Run the backend from the repository root:

```bash
.venv/bin/python -m main
```

Or keep only the lightweight launcher running and start/stop the analytics
backend from its minimal GUI:

```bash
.venv/bin/python scripts/control_server.py
```

Open `http://127.0.0.1:5400`, choose the symbol, optional expiry, and market-data
broker, then enter any required broker credentials and use **Start Backend**.
The launcher masks credentials and saves supplied values to `.env`; blank fields
preserve values already stored there. The full dashboard remains on port `5500`;
stopping it does not close the launcher page. The launcher opens this address
automatically; pass `--no-browser` when automatic browser opening is not wanted.

The same command also starts the mobile Expo server, displaying its QR code in
the terminal. Install mobile dependencies once with `cd mobile && npm install`.
The launcher page also has a **Mobile app** selector and **Start Mobile** /
**Stop Mobile** buttons. Stop the mobile server before selecting another mode.
You can also choose a startup mode from the command line:

```bash
.venv/bin/python scripts/control_server.py --mobile web
.venv/bin/python scripts/control_server.py --mobile android
.venv/bin/python scripts/control_server.py --mobile ios
.venv/bin/python scripts/control_server.py --mobile off  # launcher only
```

Expo defaults to port `8081`; override it with `--mobile-port`. Press Ctrl+C to
close the launcher and its mobile server. **Stop Backend** stops only the market
engine.

There are three services: launcher on **5400**, shared desktop/mobile backend on
**5500**, and Expo development server on **8081**. Mobile data uses
`ws://<computer-LAN-IP>:5500/mobile-ws`. The launcher enables mobile access,
creates or reuses its token in the backend `.env`, and supplies the complete
authenticated URL to Expo automatically. It also follows `--backend-port`.
There is no need to edit `mobile/.env` when using the launcher. For a computer
with multiple network adapters, use `--mobile-host <computer-Wi-Fi-IP>`.
Port **5501** is no longer used. Separate manual Expo startup still reads
`EXPO_PUBLIC_MTERMINALS_WS` from `mobile/.env`.
Only the authenticated mobile route accepts LAN access; desktop routes remain
local-only. Restart the backend and Expo after changing connection settings.
Click **Start Backend** to begin receiving market data. If mobile access was
enabled after the backend was already running, restart that backend once.

The application is served from `http://127.0.0.1:5500`; operational endpoints
are `/health` and `/metrics`. Broker credentials are required for SmartAPI mode.
Never commit `.env` or live credentials.

## Release validation

```bash
PYTHONPATH=src .venv/bin/ruff check src --select E9,F63,F7,F82
PYTHONPATH=src .venv/bin/python -m pytest src/tests

cd frontend
npm run test:release
npm run build
npm run test:e2e
```

GitHub CI is authoritative and also runs dependency vulnerability audits and
all individual architecture/product contract suites.

## Safety

Paper trading is the default. Live execution requires explicit enablement and
confirmation and remains behind account-risk checks and the kill switch. See
the [operations runbook](docs/05_Engineering/Operations_Runbook.md) before a
production start or release.
