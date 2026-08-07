# Derived Metrics


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Rules for every derived metric

Document:
1. name;
2. owner;
3. inputs;
4. units;
5. timestamp/freshness;
6. missing-input behavior;
7. live vs scenario semantics;
8. approved consumers.

## Avoid

- recomputation in templates;
- same label for different formulas;
- silent unit conversion;
- using formatted display strings as calculation inputs.

Metric ownership matrix is authoritative for shared displays.
