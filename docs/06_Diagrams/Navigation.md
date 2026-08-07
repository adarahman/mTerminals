# Navigation Flow


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


```mermaid
graph TD
  DB[Dashboard] --> OC[Option Chain]
  DB --> CF[Capital Flow]
  DB --> SC[Scenario]
  DB --> PC[Price Chart]
  DB --> PT[Paper Trading]
  OC --> SD[Strike Detail]
  CF --> OC
  DB --> SD
```
