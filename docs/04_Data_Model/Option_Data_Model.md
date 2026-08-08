# Option Data Model

> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented and CI-enforced
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.

## Identity and sides

The stable identity is `symbol + expiry + strike`. `strike` alone is only a
local key inside a snapshot already scoped to one symbol and expiry. Call and
put fields remain separate `ce*` and `pe*` namespaces and SHALL never be
substituted for one another.

## Primitive and unit contract

Rows expose LTP, bid/ask and depth where available, OI, ΔOI, volume, IV,
Greeks, signal and derived capital fields. The snapshot timestamp and source
ownership apply to the row collection.

| Fields | Canonical unit |
|---|---|
| `ceOI`, `peOI`, `ceChgOI`, `peChgOI` | lot-scaled underlying quantity |
| `ceVol`, `peVol` | raw contracts |
| LTP, bid, ask | INR per underlying unit |
| IV | percentage points |
| strike | index/underlying price points |

The payload's `dataContract.units` is the machine-readable authority.
Downstream formulas SHALL NOT multiply normalized OI by lot size again.

## Missing values

Missing is not zero. Optional IV and Greek-derived exposure serialize as JSON
`null` when unavailable. Numeric zero means an observed or valid computed zero.
Consumers SHALL render null as unavailable and SHALL NOT coerce it to zero for
analytics.

**Owner:** backend exporter. **Approved consumers:** shared market store,
Dashboard, Option Chain, reports and replay.
