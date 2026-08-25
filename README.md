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
