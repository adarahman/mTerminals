# Broker integration map

The codebase is organized as a layered architecture under `src/`. Broker
implementations live behind a stable protocol so the rest of the app never
branches on a provider. There are three broker *roles* (market-data source,
live-feed source, execution source) and each can be a different provider.

```text
                         core/ports.py
                  MarketDataProvider / ExecutionBroker (Protocols)
                               |
        +----------------------+----------------------+
        |                      |                      |
   execution              REST market-data      tick/live feed
        |                      |                      |
  brokers/connection.py  brokers/market_data_registry.py  server/feed_manager.py
  EXECUTION_ADAPTERS     runtime switch + FallbackMarketData   broker-neutral lifecycle
        |                      |                      |
  brokers/*/client.py /  brokers/*/adapter.py      server/feeds/*_feed_adapter.py
  brokers/*/execution.py (MarketData protocol)     provider-native stream -> normalized ticks
        |                      |                      |
  account/order API     wide option/futures        websocket broadcast deltas
                               |                      |
                        application/market_pipeline/  server/ (routes, bridge, app)
                               |                      |
                        market/ (option_chain, quotes,  server/websocket_clients
                        instruments, expiry, providers)
                               |
                        dashboard / API / analytics
```

The canonical option-chain runtime is import-safe and receives explicit
configuration from the application layer; it never imports a broker package
directly (it goes through `brokers/market_data_registry.py`).

## Canonical files

| Concern | Use this file | Responsibility |
| --- | --- | --- |
| Broker contracts | `core/ports.py` | `MarketDataProvider` / `ExecutionBroker` runtime-checkable Protocols. |
| Provider metadata | `brokers/provider_registry.py` | `ProviderSpec` (display label, snapshot/websocket/execution caps) and `PROVIDER_SPECS`. |
| Market-data routing | `brokers/market_data_registry.py` | The switchable, fallback-wrapped provider facade. All option-chain code uses this, not a broker package. |
| Execution routing | `brokers/connection.py` | Loads one normalized order/account adapter via `EXECUTION_ADAPTERS` and runs readiness checks. |
| Live-feed routing | `server/feed_manager.py` | Broker-neutral lifecycle for a provider's persistent tick feed. |
| Market-data pipeline | `application/market_pipeline/` | Canonical broker-neutral pipeline (coordinator, quotes, option_chain, futures) producing wide DataFrames. |
| History API | `server/market_history_api.py` | SmartAPI-backed candle history, decoupled from the active feed source. |
| Live orchestration | `src/server/app.py` (composition root) | Owns process-wide runtime state, starts/stops feeds, wires `server/*` into the running process. |

## Provider-specific files

Each broker is a package under `brokers/<provider>/`:

| File | Purpose | Must not do |
| --- | --- | --- |
| `client.py` | Native SDK/API, credentials, auth. | Depend on dashboard rendering or pipeline DataFrames. |
| `adapter.py` / `market_data.py` | Translate raw REST into the `MarketDataProvider` protocol. | Route orders or broadcast WebSocket messages. |
| `execution.py` (only if supported) | Apply app config and normalize the shared order/account contract. | Build option-chain data. |
| `server/feeds/<provider>_feed_adapter.py` | Normalize native socket messages into the common tick schema. | Compute option-chain metrics or touch the dashboard. |

## Adding a broker

1. Create `brokers/<provider>/{client,adapter,market_data}.py` and satisfy
   `MarketDataProvider` (see `core/ports.py`). Add it to
   `brokers/provider_registry.py` with its capabilities.
2. Register the runtime factory in `brokers/market_data_registry.py`'s
   `_provider_classes()` and add a credential check to `provider_has_credentials`.
3. Add an execution module to `brokers/connection.py::EXECUTION_ADAPTERS`
   only if order/account operations are implemented.
4. Add a `server/feeds/<provider>_feed_adapter.py` only if the broker offers a
   supported live feed; it must emit the shared normalized tick shape.
5. Do not add a new pipeline or feed aggregator for the broker.
