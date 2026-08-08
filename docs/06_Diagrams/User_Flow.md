# Trader User Flow

> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

```mermaid
flowchart LR
  OPEN["Open Dashboard"] --> FEED{"Feed usable?"}
  FEED -- "No" --> WAIT["Read reason; wait or recover"]
  WAIT --> FEED
  FEED -- "Yes" --> DEC["Read decision and evidence confidence"]
  DEC --> VERIFY["Verify structure, flow and institutional evidence"]
  VERIFY --> DRILL{"Need strike detail?"}
  DRILL -- "Yes" --> CHAIN["Inspect Option Chain and Strike Detail"]
  DRILL -- "No" --> SCEN
  CHAIN --> SCEN{"Need scenario validation?"}
  SCEN -- "Yes" --> SIM["Run isolated scenario / strategy analysis"]
  SCEN -- "No" --> MODE
  SIM --> MODE{"Execution mode"}
  MODE -- "Paper" --> PAPER["Place guarded paper order"]
  MODE -- "Explicit live" --> RISK["Confirm live mode and risk gates"]
  RISK --> LIVE["Submit broker order"]
  PAPER --> MON["Monitor position, feed and thesis"]
  LIVE --> MON
  MON --> DEC
```

Degraded evidence disables execute-ready recommendations. Scenario results do
not overwrite the live decision baseline.
