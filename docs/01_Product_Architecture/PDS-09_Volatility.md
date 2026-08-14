# PDS-09 — Volatility (D-13)


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-12 project snapshot  
> **Status:** Authoritative design target unless marked otherwise — Stage 2/3 metrics below are explicitly gated on backend work not yet done (see "Backend readiness")  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Mission

Answer:

> **Does current volatility context support or undermine the Decision Engine's call?**

Volatility is Confirmation-zone, Tier 2–3. It SHALL never issue its own trade
call and SHALL never be confused with D-01's decision.

## Current analytical foundations

As of this baseline, the backend already computes, live, every tick:
- India VIX level (`fetch_vix_smartapi`, already displayed on the ticker strip);
- `vix_regime` (Low/Normal/High, `engine.py`, thresholds from `decision/types.py`'s
  `T.VIX_LOW`/`T.VIX_NORMAL`);
- current-expiry ATM IV (`base_iv`, already used chain-wide);
- current-expiry per-strike live CE/PE IV (`oi/pricing.py`'s `get_iv_skew` +
  `chain_metrics.py`'s live-IV-with-synthetic-fallback logic) — this is a live
  cross-sectional skew already computed every tick, just never charted;
- an `iv_rank`/`hv30` pair (`chain_metrics.py`'s `_compute_iv_rank_hv30`),
  already surfaced in strategy grading (`mTerminals_json.py`'s `_grade()`).

## Backend readiness — read before scoping any milestone

**`iv_rank` and `hv30` are not currently real values.** `_compute_iv_rank_hv30`
requires ≥2 historical rows to compute anything; its only caller passes
`df_full_history = build_oi_history(df_clean, SYMBOL, prev_poll=prev_json_poll)`,
which is rebuilt fresh every tick from a single-snapshot diff against the
immediately-prior tick — it is never accumulated across ticks or days. In
practice this means the `len(iv_series) >= 2` / `len(spot_series) >= 5`
guards inside `_compute_iv_rank_hv30` are not met, and both values silently
fall back to their hardcoded stubs (`iv_rank = 35.0`, `hv30 = base_iv * 85`)
on effectively every tick. This SHALL be fixed before IV Rank or HV30 ship
as real UI values — displaying the stub as if it were live would be a false
signal, worse than showing "unavailable."

The fix is a persistent daily-close store (one ATM-IV + one spot value per
trading day), not an intraday accumulator — `_compute_iv_rank_hv30`'s own
`iv_rank_window=252`/`hv_window=30` are daily-bar windows. The existing
`nse_fii_dii_flow_history.csv` accumulated-daily-history pattern is the
precedent to follow, not a new intraday buffer.

**IV term structure (ATM IV per expiry) has an unresolved data-availability
question.** `market_api.py`'s NSE fetch logs `expiries=N` from the full
multi-expiry `expiryDates` list in NSE's response, but the live pipeline's
primary chain source is SmartAPI REST (`option_chain_json.py`/
`smartapi_pipeline_adapter.py`) per this project's own startup banner —
`market_api.py` is now used only for `fetch_all_indices()`. Whether
multi-expiry ATM IV can be extracted cheaply from data already being pulled,
or requires new per-expiry SmartAPI calls (each with its own rate-limit
cost, see the SmartAPI rate-limit incident history), SHALL be verified
before Term Structure is scoped into an implementation milestone.

## Core metric families

### Stage 1 — approved display foundation (buildable now, no backend work)
- India VIX (level + regime badge);
- current-expiry ATM IV;
- current-expiry IV skew — call vs put IV plotted across strikes, cross-
  sectional, computed fresh from the current tick's chain rows. No history
  dependency.

### Stage 2 — conditional on the persistent daily-close store above
- IV Rank (0–100, current ATM IV's position within its historical range);
- IV Percentile (% of historical days at or below current ATM IV — SHALL be
  implemented and labeled as a distinct metric from IV Rank, not a
  restatement of it: Rank is min–max normalized, Percentile is a rank-count
  statistic, and they can diverge materially on skewed distributions);
- HV30 (30-day realized volatility) and the IV-vs-HV divergence relationship;
- a VIX/ATM-IV trend line across trading days.

### Stage 3 — conditional on the term-structure data-availability question above
- IV term structure (ATM IV across expiries, near to far).

## Product separation

**D-13** owns volatility *context and interpretation* for confirmation
purposes — is volatility high, low, rising, or diverging from realized
movement, and does that support the current decision.

**D-01** (Decision Engine) MAY reference D-13's `vix_regime` as supporting
evidence in its own confidence language, but SHALL NOT recompute a
duplicate VIX-regime classification — D-13 is the single canonical owner
per this project's existing metric-ownership rule.

**D-03** (Greeks/GEX) owns Vega exposure and gamma-related volatility
*sensitivity* of the current book — a different question ("how exposed am I
to a vol move") from D-13's ("is the current vol level itself notable").
These SHALL NOT be merged into one card.

## Per-metric unit contract

- All IV figures SHALL display as percent (e.g. `14.2%`), matching the
  chain table's existing `CE_IV`/`PE_IV` convention — never as a raw
  decimal.
- IV Rank and IV Percentile SHALL both display as `0–100`, each explicitly
  labeled, never merged into one number.
- HV30 SHALL be labeled with its window (`HV30`, not bare "HV") since other
  realized-vol windows may be added later.

## Degradation

- If the daily-close store has fewer than 2 days of history, IV
  Rank/Percentile/HV30/trend SHALL show an explicit "Accumulating history"
  state — never the stub values, and never a blank/zero that could be
  misread as "0% rank."
- If term structure's data source is unavailable for a given expiry, that
  expiry's point SHALL be omitted from the curve, not interpolated or
  zero-filled.
- A stale VIX quote (feed disconnect) SHALL be visibly flagged per D-00's
  existing feed-staleness contract, not silently frozen.

## Confirmation-only rule

Volatility context SHALL NOT independently trigger `executeRecommended` or
any auto-execution path. It qualifies D-01's confidence language only when
D-01 chooses to reference it (see Product separation above) — matching the
existing rule that Confirmation-zone cards SHALL NOT overpower D-01.

## Acceptance

1. Stage 1 metrics (VIX, VIX regime, ATM IV, IV skew chart) are real values
   sourced from the live tick, with no stub/placeholder path.
2. IV Rank and IV Percentile never render until the backend's persistent
   daily-close store has real history to compute from; until then the card
   shows an explicit "Accumulating history" state, not a numeric stub.
3. IV Rank and IV Percentile are visually and semantically distinguishable
   as two different metrics, never collapsed into one label.
4. Term structure renders only once its data-availability question is
   resolved and documented; it is not shipped as a silently-approximated
   or single-expiry-repeated placeholder.
5. D-13 never issues a buy/sell/hold call of its own; it only appears as
   supporting language inside D-01 when D-01 references it.
6. A feed disconnect is visible on D-13 (via D-00's existing contract), not
   a frozen last-good VIX value presented as current.
