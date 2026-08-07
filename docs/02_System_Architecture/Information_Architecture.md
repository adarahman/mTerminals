# Information Architecture


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Primary product flow

```text
Observe → Decide → Explain → Investigate → Simulate/Execute → Monitor
```

## Surfaces

| Surface | Role |
|---|---|
| Dashboard | Executive decision and evidence |
| Option Chain | Dense strike analysis |
| Capital/OI Flow | Fresh flow investigation |
| Strike Detail | Single-strike explanation |
| Scenario | Counterfactual analysis |
| Price Chart | Price-action context |
| Paper Trading | Simulated execution and monitoring |

## Rule

A surface exists because it answers a distinct user question. File ownership
or backend module boundaries do not create navigation categories.

## Dashboard zones

Status → Decision → Structure → Capital Flow → Institutional → Confirmation.

## Tier rules

Tier 1 information is immediately visible; Tier 2 is explanatory; Tier 3 is
deliberately opened. Deep content moves outward rather than increasing executive
card height.

## Information duplication

Duplication is permitted only when:
- it reduces navigation cost; and
- the duplicate is a canonical read-only reference.

Duplicate calculations under the same label are prohibited.
