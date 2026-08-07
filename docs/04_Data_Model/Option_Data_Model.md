# Option Data Model


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Row identity

Minimum stable identity:
`symbol + expiry + strike`.

## Sides

Call and Put fields remain semantically separate even when normalized into one strike row.

## Typical primitives

LTP, bid/ask where available, OI, ΔOI, volume, IV, Greeks, timestamp/source flags.

## Unit contract

The schema SHALL record whether OI is:
- contract count;
- lot-scaled quantity;
- another normalized quantity.

Downstream capital formulas depend on this distinction.

## Missing values

Missing ≠ zero. Serialization and UI shall preserve that distinction.
