# PDS-05 — Decision Engine


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Mission

The Decision Engine converts approved evidence into a concise trading posture.

It answers:

> **What is the system's current bias, how strong is the evidence, and what
> invalidates the thesis?**

## Current implementation

Backend package:
- `decision/decision_engine.py`
- `decision/confidence.py`
- `decision/signal_builder.py`
- `decision/strategy_selection.py`
- `decision/types.py`
- `decision/auto_executor.py`

These modules SHALL remain separated from presentation code.

## Output contract

The decision state SHOULD be representable as a typed object containing at least:

- bias/action;
- confidence;
- trade grade;
- rationale/evidence;
- risk/trap warning;
- important levels;
- selected strategy where applicable;
- timestamp/state version;
- degraded-input indicators.

## Confidence semantics

Confidence is **evidence agreement**, not probability of profit and never a guarantee.

Rules:
- no double counting simply because a signal appears in multiple UI cards;
- unavailable critical inputs reduce/qualify confidence;
- confidence and bias are separate;
- UI does not calculate confidence;
- component scores SHOULD be inspectable in Tier 3.

## Decision/evidence boundary

Decision code may consume:
- chain positioning;
- Greeks/GEX;
- capital flow;
- institutional analytics;
- volatility/regime;
- risk constraints.

It SHALL NOT consume rendered DOM, card visibility or CSS state.

## Strategy selection

Strategy selection is downstream of the market decision and risk eligibility.
A strategy name SHALL not be treated as the underlying market signal itself.

## Automation boundary

`auto_executor.py` is execution orchestration, not Decision UI.
A decision being strong does not bypass account/risk guards.

## Degraded mode

When required evidence is stale/missing:
- decision may remain visible as last valid;
- state must be marked stale/degraded;
- confidence must not imply full evidence coverage;
- auto execution SHALL obey separate safety/risk rules.

## Acceptance

1. Same canonical state yields same decision regardless of which cards are visible.
2. UI cannot silently change confidence logic.
3. Confidence has an explainable contributor set.
4. Risk/execution gates remain separate from analytical confidence.
5. Decision state is timestamp coherent.
