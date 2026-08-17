# fno-dashboard-backend

NSE F&O options-chain analytics, decision engine, and paper/live trading
backend for AngelOne (SmartAPI).

## Install

```bash
pip install -e .
# development/test dependencies:
pip install -e ".[dev]"
```

Requires Python ≥3.10.

## Configuration

Broker credentials are read from environment variables (via `.env`,
loaded by `brokers/smartapi_client.py` on import). Create a `.env` file
in the project root:

```bash
SMARTAPI_KEY=your_api_key
SMARTAPI_CLIENT_CODE=your_client_code
SMARTAPI_PIN=your_pin
SMARTAPI_TOTP_SECRET=your_totp_secret
```

Shoonya can be selected for account state and live execution while SmartAPI
continues to supply the dashboard market-data feed:

```bash
./scripts/setup_shoonya.sh
cp .env.shoonya.example /tmp/shoonya-settings.example  # reference only
```

Copy the `SHOONYA_*` keys from `.env.shoonya.example` into `.env`, fill them
locally, and set `EXECUTION_BROKER=SHOONYA`. Do not paste or commit credentials.
Live execution remains subject to `LIVE_TRADING_ENABLED` and the kill switch.

`.env` is gitignored — never commit real credentials. See `paths.py` for
other path/cache configuration.

## Layout

```
brokers/     AngelOne SmartAPI session, REST + WebSocket clients, instrument lookup
oi/          Option-chain master table, OI analysis/velocity, Black-Scholes pricing
risk/        Risk-meter gauges (Delta/Gamma/Vega/Theta/Liquidity/Event/Concentration)
decision/    Sub-signal scoring, bias/confidence, strategy suggestion (DecisionEngine)
strategy/    Strategy definitions, scoring, scenario P&L
storage/     Shared cache primitives (TTL, memoized, rolling-window, tick-scoped)
analytics/   FII/DII flow and sentiment
ml/          Feature building, training, inference for the ML layer
engine.py    Single computation pass — builds one EngineResult per tick
paper_trading.py   Standalone SQLite-backed paper trading engine
```

`engine.py`'s `build_engine_result()` is the single entry point that
computes Greeks, OI metrics, risk meters, and strategy suggestions once
per tick; everything downstream (JSON export, decision engine, dashboards)
reads off the returned `EngineResult` rather than recomputing anything.

## Running tests

```bash
pip install -e ".[dev]"
pytest
```

The suite currently contains 205 deterministic tests spanning decision, risk,
capital, SmartAPI adapters, paper/live order guards, reconciliation, backtest,
health, logging, readiness and architecture boundaries. GitHub CI is the
authoritative release result.

## Notes

- `runtime/cache/` holds regenerated/accumulating state (ScripMaster
  cache, OI history parquet log) — gitignored, not source.
- `paper_trading.py` is fully decoupled from the live market-data
  pipeline: it only prices fills against whatever LTP you feed it, and
  persists orders/positions to a local SQLite file (`paper_trading.db`,
  also gitignored).
