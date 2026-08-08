# Decision Model

> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

## Canonical object

A decision exposes bias and strength, action/type, confidence, trade grade,
evidence contributors, active signals, warnings, important levels, strategy,
execution recommendation, timestamp, state version and degraded-input flags.
Confidence is bounded evidence confidence, not a probability of profit.

## Provenance and safety

`decisionTimestamp` and `stateVersion` bind the decision to one exported market
state. `evidenceCoverage`, `contributors`, and `missingInputs` explain its data
quality. Missing critical evidence forces `degraded=true`, disables execution,
and produces a WAIT/caution outcome. UI visibility does not affect decision
state and the frontend SHALL NOT recompute confidence.

Live decision output is derived only from canonical live analytics. Scenario
adjustments remain isolated and cannot mutate the decision baseline.

**Owner:** `decision.DecisionEngine`. **Approved consumers:** decision view,
strategy presentation, guarded automation, snapshot replay and backtests.
