# Data Flow


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Market path

```text
SmartAPI / NSE
   ↓
broker / market adapters
   ↓
normalization + cache
   ↓
OI / Greeks / analytics
   ↓
decision output
   ↓
mTerminals JSON / WebSocket payload
   ↓
WSManager
   ↓
MarketStore
   ↓
view-models / panels
   ↓
DOM / canvas
```

## Paper-trading path

```text
User order
  ↓
validation
  ↓
paper backend
  ↓
simulated order/fill
  ↓
portfolio/account state
  ↓
paper UI
```

Paper state SHALL not flow backward into market analytics except where a separate,
explicit position-aware risk function is designed.

## Historical/chart path

Historical loader feeds PriceChart history while current live ticks update the
latest series. These paths SHOULD be independently recoverable.

## Unit discipline

Data-flow boundaries SHALL document:
- contracts vs quantity;
- lot-scaled vs raw;
- currency units;
- percentages vs decimals;
- timestamps/time zones.
