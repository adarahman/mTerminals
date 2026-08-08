# Navigation Flow

> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

```mermaid
flowchart TD
  DB["Dashboard"] -->|"scroll zones"| EVIDENCE["Structure · Capital · Institutional · Confirmation"]
  DB -->|"open"| OC["Option Chain"]
  DB -->|"open modal"| OI["OI Flow / Greeks / FII-DII / Backtest"]
  DB -->|"open dedicated surface"| PC["Price Chart"]
  DB -->|"persistent panel"| PT["Paper Trading"]
  DB -->|"expand"| SC["Scenario / Strategy detail"]
  OC -->|"selected strike"| SD["Dashboard Strike Detail dialog"]
  SD -->|"close; restore context"| DB
  EVIDENCE -->|"strike link"| OC
  PC -->|"shared symbol context"| DB
```

Symbol and expiry context propagate explicitly. Back/close actions restore the
invoker, selected strike, scroll position and active market context.
