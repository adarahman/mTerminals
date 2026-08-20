# Broker integration map

There are three independent paths. They can use different brokers and must
not be confused with each other.

```text
                         config.py
       EXECUTION_BROKER / MARKET_DATA_PROVIDER / LIVE_FEED_PROVIDER
                              |
        +---------------------+---------------------+
        |                     |                     |
   execution              REST snapshots         tick stream
        |                     |                     |
 connection.py        brokers/market_data.py   *_ws_client.py
 registry + health     stable provider facade   provider-native socket
        |                     |                     |
 *_client.py /         smartapi_pipeline_       smartapi_feed_adapter.
 *_execution_adapter   adapter.py               TickAggregator
        |                     |                     |
 account/order API     wide option/futures      websocket broadcast deltas
        +---------------------+---------------------+
                              |
                       ws_server_live.py
                              |
                       dashboard websocket
```

## Canonical files

| Concern | Use this file | Responsibility |
| --- | --- | --- |
| Runtime choices | `config.py` | Reads the three broker-role settings. |
| Execution routing | `brokers/connection.py` | Loads one normalized order/account adapter and performs readiness checks. |
| Market-data routing | `brokers/market_data.py` | The stable, switchable REST provider facade. All option-chain code should use its `market_data` object. |
| REST pipeline | `broker_pipeline.py` | Canonical broker-neutral REST pipeline. Converts `market_data` results to the wide DataFrames used by the engine. |
| Tick aggregation | `tick_pipeline.py` | Canonical provider-neutral `TickAggregator` that converts normalized socket ticks to dashboard deltas. |
| Live orchestration | `ws_server_live.py` | Chooses roles, starts/stops feeds, invokes the pipeline, and broadcasts results. It should not implement a broker protocol. |

## Provider-specific files

| File pattern | Purpose | Must not do |
| --- | --- | --- |
| `*_client.py` | Native SDK/API, credentials, and provider-specific requests. | Depend on dashboard rendering or pipeline DataFrames. |
| `*_market_data.py` | Translate a provider's raw REST response into the `MarketData` protocol. | Route orders or broadcast WebSocket messages. |
| `*_execution_adapter.py` | Apply app configuration and normalize the shared order/account contract. | Build option-chain data. |
| `*_ws_client.py` | Normalize native socket messages into the common tick schema. | Compute option-chain metrics or touch the dashboard. |

## Compatibility names to avoid in new code

- `smartapi_feed_adapter.py` remains a compatible import path; new code
  imports `TickAggregator` from `tick_pipeline.py`.
- `smartapi_pipeline_adapter.py` remains the compatible implementation path;
  new code imports its broker-neutral API from `broker_pipeline.py`.
- `market_api.py` is the public NSE/BSE source, not the broker-provider
  registry. New broker code belongs under `brokers/`.

## Adding a broker

1. Add its REST implementation to `brokers/market_data.py`'s provider
   registry, using a provider-specific `*_market_data.py` file where needed.
2. Add an execution adapter to `brokers/connection.py::EXECUTION_ADAPTERS`
   only if order/account operations are implemented.
3. Add a `*_ws_client.py` only if the broker offers a supported live feed; it
   must emit the shared normalized tick shape.
4. Do not add a new pipeline or feed aggregator for the broker.
