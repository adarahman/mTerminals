# mTerminals backend

This directory contains the Python backend for the mTerminals market dashboard.
The product overview and complete local setup instructions are maintained in
the repository [README](../README.md); backend architecture and operations
guides are indexed in [docs/00_MASTER_INDEX.md](../docs/00_MASTER_INDEX.md).

## Install and run

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e 'src[dev]'
.venv/bin/python -m main
```

The backend requires Python 3.10 or newer. The package metadata in this
directory is the canonical source for Python runtime and development
dependencies. Broker credentials belong in the repository-root `.env` file;
see `.env.brokers.example`. Never commit real credentials.

Paper trading is the default. Live execution must be explicitly enabled and is
guarded by account-risk checks and the kill switch. Read the operations runbook
before enabling live execution.

## Backend checks

```bash
cd src
../.venv/bin/python -m pytest
```
