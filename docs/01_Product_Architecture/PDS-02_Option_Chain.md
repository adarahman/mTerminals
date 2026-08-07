# PDS-02 — Option Chain


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Version:** 1.1
> **Status:** Implemented — P0/P1/P2 closure aligned 2026-08-07
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Mission

The Option Chain is the authoritative dense strike-analysis surface. Its job is
to answer:

> **What is happening at each strike, and where is the meaningful positioning?**

It SHALL be analytical, fast and stable under live updates.

## Primary surface

Current implementation: `frontend/OptionChain/option-chain.html`,
`option-chain.js`, `option-chain.css`, with Dashboard chain logic shared through
`Dashboard/chain/*`.

## Information hierarchy

### Tier 1
- Symbol/expiry context inherited from global state.
- Spot/ATM.
- Range selection (±3/±5/±10/±15/All where supported).
- Clear call/strike/put column grouping.

### Tier 2
Per strike:
- OI and ΔOI;
- volume;
- LTP;
- IV;
- Greeks where available;
- canonical per-strike institutional primitive;
- capital/exposure fields approved by Metric Ownership.

### Tier 3
- inline Greek expansion;
- Strike Detail Report;
- specialist overlays/diagnostics.

## Layout contract

The strike column is the visual anchor. Call and Put data SHALL remain
unambiguously separated. The dense ledger MAY use paired bilateral PE/CE stacks
inside a metric column instead of literal left/right CALL and PUT halves, provided
that PE/CE text labels remain explicit and side identity never relies on color alone.
ATM SHALL be visually distinct but not so dominant that neighboring strikes become unreadable.

The table SHALL maintain stable column order during a session.

## Context contract

- Expiry follows the shared/global selection when launched from Dashboard.
- Feed/session semantics SHALL reuse shared application state, including PARTIAL, MARKET CLOSED and HOLIDAY where available.
- A standalone Option Chain MAY expose context controls, but it SHALL synchronize
  them through the application state model rather than maintain contradictory state.
- Range selection changes visibility, not analytics.

## Interaction contract

- Clicking a strike expands/toggles inline details where specified.
- Opening Strike Detail is a deliberate action and SHALL not occur accidentally
  when selecting text or using table controls.
- Scroll position survives ticks.
- ATM auto-centering occurs on initial context load, not continuously.
- Range changes SHOULD re-center ATM only when necessary for coherence.

## Data ownership

The Option Chain is the canonical presentation owner for **per-strike primitives**.
Whole-market interpretation belongs to executive analytics.

It SHALL NOT become the canonical owner of:
- Decision bias;
- whole-chain Smart Money interpretation;
- market regime;
- scenario-adjusted outputs.

## Live update contract

Prefer field/row patching. A full `<tbody>` rebuild on every tick is a migration
target, not the desired end state.

A live tick SHALL preserve:
- scroll;
- selected strike;
- expanded row;
- keyboard focus where practical;
- user range selection.

## Missing data

Unavailable Greeks/IV SHALL display unavailable state, not zero.
A strike row MAY remain visible with partial fields.

## Performance

- Diff by strike key.
- Avoid DOM writes when displayed values do not change.
- Batch rapid deltas.
- Do not re-run expensive formatting for unchanged rows.
- Chart/overlay updates are independently invalidated.

## Accessibility

- Header cells expose proper semantics.
- Selected/ATM states are not color-only.
- Strike actions have keyboard equivalents.
- Dense tables remain zoom-usable.

## Acceptance criteria

1. ATM is correctly identifiable after initial load.
2. Manual scroll is never reset by a normal tick.
3. Range selection does not alter underlying canonical values.
4. No `NaN`/`undefined` is exposed.
5. Expanded strike detail survives unrelated deltas.
6. Expiry is synchronized with Dashboard context.
7. Per-strike metrics agree with Dashboard consumers at the same state version.
8. Compact viewport does not force the entire application page to table width.

## Implementation closure

As of 2026-08-07, PDS-02 acceptance criteria are implemented. Net OI Flow 5m/15m/30m belongs to Dashboard D-07 rather than the D-05 strike ledger. D-05 retains OI, ΔOI, volume, LTP, IV, premium locked, canonical footprint, signal/structure, inline Greeks and deliberate Strike Detail drill-down. Further field-level row-patching is performance hardening and SHOULD be driven by profiling rather than a redesign.
