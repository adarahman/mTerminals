# Component Tree

> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

```mermaid
flowchart TD
  PAGE["DashboardPro page"] --> BOOT["Dashboard bootstrap"]
  BOOT --> DS["DataService"]
  BOOT --> PM["PanelManager"]
  BOOT --> MM["ModalManager"]
  DS --> WM["WSManager"]
  DS --> MS["MarketStore"]
  MS --> AS["AppState"]
  AS --> RENDER["Chain renderer"]
  PM --> RENDER
  RENDER --> DEC["Decision and evidence views"]
  RENDER --> FLOW["Capital / OI flow views"]
  RENDER --> INST["Institutional views"]
  RENDER --> CONF["Confirmation views"]
  MM --> DRILL["OI, Greeks, FII/DII, strike and backtest dialogs"]
  PAGE --> TRADE["Paper trading + order entry"]
  PAGE --> ALGO["Automation status"]
```

The tree shows runtime ownership, not DOM nesting. `EventBus` carries semantic
notifications beside this tree; it does not replace `MarketStore`.
