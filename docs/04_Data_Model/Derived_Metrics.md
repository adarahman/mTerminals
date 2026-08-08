# Derived Metrics

> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced

## Required metadata

Every shared metric SHALL define its name, owner, inputs, units,
timestamp/freshness, missing-input behavior, live/scenario semantics and
approved consumers. The payload `dataContract` publishes common units,
nullability, provenance and freshness ownership at the serialization boundary.

## Ownership matrix

| Family | Canonical owner | Missing behavior | Approved consumers |
|---|---|---|---|
| Option primitives | exporter/master table | null where optional | shared store and views |
| Capital metrics | `oi.capital_metrics` | Stage-1 skips unavailable in totals; Stage-2 fails closed | Dashboard and reports |
| Greeks/GEX | analytics engine | null when unverified | Dashboard, scenario, strategy |
| Institutional footprint | `oi.footprint_score` | absent/unknown, never identity inference | institutional views |
| Decision | `decision.DecisionEngine` | degraded WAIT with missing inputs | decision views and execution gate |
| Scenario | scenario engine | isolated from live namespace | simulator only |

Templates and views SHALL format canonical values but SHALL NOT recompute shared
formulas, silently convert units, or use formatted strings as inputs. The same
label SHALL NOT represent different formulas or scopes.
