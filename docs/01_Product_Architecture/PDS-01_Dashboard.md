# PDS-01 — Dashboard

| Field | Value |
|---|---|
| **Version** | 1.1 |
| **Status** | Complete — Ready for implementation review |
| **Product** | mTerminals |
| **Surface** | `DashboardPro.html` |
| **Owner** | Product Design |
| **Depends on** | PDS-00 — Global Design System |
| **Last Updated** | 2026-08-07 |
| **Change from 1.1** | Aligns D-05 with the implemented dedicated Option Chain surface; revises drill-down, layout, navigation, and acceptance criteria |

---

## 0. Authority and Scope

This document is the authoritative product and interaction specification for the mTerminals main Dashboard. It defines **what belongs on the Dashboard, why it exists, where it appears, what owns each shared metric, how live data is allowed to update it, and how the user interacts with it**.

This document deliberately does **not** prescribe concrete HTML, CSS selectors, JavaScript class names, backend Python functions, or bundler configuration. Those are implementation details and may evolve without changing this PDS, provided the observable product contracts defined here remain true.

Where an implementation decision conflicts with this PDS, the implementation SHALL be changed or this PDS SHALL be explicitly revised. Silent divergence is not permitted.

### 0.1 In scope

- Main Dashboard information architecture.
- Zone and card order.
- Tier-1 / Tier-2 / Tier-3 information hierarchy.
- Metric display ownership.
- Cross-card dependencies.
- Live-update behavior.
- Navigation and drill-down behavior.
- Loading, stale, partial-data and error states.
- Keyboard and accessibility requirements.
- Desktop and compact layouts.
- Performance and rendering contracts.
- Acceptance criteria.

### 0.2 Out of scope

- Exact calculation formulas already owned by domain analytics modules.
- Broker/API authentication.
- Historical database design.
- Phone-specific layout.
- Exact visual token values where PDS-00 is authoritative.
- Trading execution logic.
- Backtest engine internals.

---

# 1. Mission

The Dashboard exists to answer one question, fast, every time a trader opens it:

> **What should I do right now, and how sure am I?**

The Dashboard is therefore a **decision instrument**, not a collection of analytics widgets.

A user who reads only the Status Bar and Decision Engine SHALL already have a complete actionable interpretation of the current market state. Everything below exists to explain, validate, challenge, or investigate that interpretation.

---

# 2. Product Principles

## D-1 — One question per card

Every card SHALL answer exactly one primary trading question.

A card that answers two unrelated questions SHALL be split. A metric that does not help answer the card's question SHALL not be added merely because space exists.

## D-2 — One canonical owner per metric

A shared metric SHALL have one authoritative display owner. Other cards may reference the same computed value, but SHALL NOT independently derive a second value under the same label.

Legitimately different calculations SHALL use qualified labels, for example:

- `Live Gamma Flip`
- `Scenario-Adjusted Gamma Flip`

They SHALL never both appear simply as `Gamma Flip`.

## D-3 — Group by trading workflow

Cards are grouped according to the question a trader is asking:

1. Is the feed valid?
2. What should I do?
3. Where is positioning concentrated?
4. Where is money moving?
5. Who appears to be moving it?
6. Does the supporting evidence confirm the call?

Backend package boundaries SHALL NOT determine visual grouping.

## D-4 — Progressive disclosure

Information has three tiers:

| Tier | Meaning | Default |
|---|---|---|
| **Tier 1 — Decision** | Understandable in under one second | Always visible |
| **Tier 2 — Explanation** | Requires scanning | Visible in open zones |
| **Tier 3 — Exploration** | Deliberate deep analysis | Modal/collapsible/detail surface |

## D-5 — Deep analysis does not enlarge executive cards

A new power-user capability SHALL move into Tier 3 rather than making the default card taller or denser.

## D-6 — Decision before evidence

Supporting evidence SHALL never visually compete with the Decision Engine.

## D-7 — Stable spatial memory

Runtime values SHALL NOT reorder zones or cards. A trader must learn where information lives and find it in the same place every time.

## D-8 — Live data must be calm

The interface SHALL update continuously without behaving as though it is continuously rebuilding.

---

# 3. Reading Flow

The page SHALL read in this exact order:

```text
STATUS
Is the feed valid? What symbol and expiry?
        │
        ▼
DECISION ENGINE
What should I do? How confident is the system?
        │
        ▼
STRUCTURE & POSITIONING
Where is positioning concentrated? What gamma regime exists?
        │
        ▼
CAPITAL FLOW
Where is money moving now?
        │
        ▼
INSTITUTIONAL
What activity appears institutionally significant?
        │
        ▼
CONFIRMATION
Does secondary evidence support or contradict the call?
```

Priority decreases monotonically down the page.

The Decision Engine is the visually dominant element. No lower zone heading, card, chart, alert, or animation may become visually heavier during normal operation.

---

# 4. Zone Model

| Zone | Question | Tier | Default | Entry rationale |
|---|---|---:|---|---|
| **Status** | Is the data usable and what contract am I viewing? | 1 | Visible | A decision is meaningless without feed/context validity. |
| **Decision Engine** | What should I do and how sure is the system? | 1 | Visible | Primary product output. |
| **Structure & Positioning** | Where is positioning concentrated and what regime exists? | 1–2 | Open | Explains the structural basis of the decision. |
| **Capital Flow** | Where is money moving intraday? | 1–2 | Open | Distinguishes static positioning from active participation. |
| **Institutional** | Where does activity appear institutionally significant? | 2 | Open | Adds participant-quality/context evidence. |
| **Confirmation** | Does secondary evidence agree? | 2–3 | Collapsed | Useful verification, but not required for first-glance action. |

---

# 5. Card Inventory

Component IDs are durable identifiers.

| ID | Card | Zone | Tier | Primary question |
|---|---|---|---|---|
| D-00 | Status Bar | Status | 1 | Is the feed current and what am I viewing? |
| D-01 | Decision Engine | Decision | 1 + 3 | What should I do and how confident is the call? |
| D-02 | Market Health & Story | Structure | 1–2 | What is the simplest coherent story of the current market? |
| D-03 | Greeks / Net GEX Alerts | Structure | 1–2 + 3 | What gamma/Greeks conditions materially affect the trade? |
| D-04 | Option Chain Snapshot | Structure | 1–2 | What does aggregate option positioning say? |
| D-05 | Option Chain | Dedicated Tier-3 surface | 3 | What is happening strike by strike? |
| D-06 | Greeks by Moneyness | Structure | 2 | How do Greeks change across strikes? |
| D-07 | Vol/OI Velocity + OI Flow | Capital Flow | 1–2 + 3 | Where is fresh derivatives activity accelerating? |
| D-08 | FII/DII Summary | Capital Flow | 1–2 + 3 | What does institutional cash flow contribute? |
| D-09 | Market Regime & Smart Money | Institutional | 2 | What regime and smart-money posture best describe the market? |
| D-10 | Institutional Footprint Score | Institutional | 2 | How strong is the institutional footprint? |
| D-11 | Capital Concentration | Institutional | 2 | Where is meaningful capital concentrated? |
| D-12 | Institutional Activity Crux | Institutional | 2 + 3 | Which near-ATM strikes deserve investigation? |
| D-13 | Volatility | Confirmation | 2–3 | Does volatility context support the decision? |
| D-14 | Probability | Confirmation | 2–3 | Which chain outcomes rank highest probabilistically? |
| D-15 | Scenario Analysis | Confirmation | 3 | How does the thesis behave under alternative states? |
| D-16 | Advanced Analytics | Confirmation | 3 | What specialist evidence is available for deliberate inspection? |
| D-17 | Strategy Simulator | Confirmation | 3 | How do active strategies behave under simulated conditions? |
| D-18 | Paper Trading | Persistent | 1 pill / 2 panel | What is the paper account state? |
| D-19 | Price Chart | Persistent | — | Where can price action be inspected in its dedicated surface? |

---

# 6. Grid and Spatial Architecture

## 6.1 Desktop — width ≥ 1280px

```text
┌──────────────────────────────────────────────────────────────┐
│ D-00 STATUS                                                  │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ D-01 DECISION ENGINE                                         │
└──────────────────────────────────────────────────────────────┘

STRUCTURE & POSITIONING
┌──────────────────┬──────────────────┬────────────────────────┐
│ D-02 Story       │ D-03 GEX/Greeks │ D-04 Chain Snapshot    │
└──────────────────┴──────────────────┴────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ D-06 GREEKS BY MONEYNESS                                     │
└──────────────────────────────────────────────────────────────┘

CAPITAL FLOW
┌─────────────────────────────┬────────────────────────────────┐
│ D-07 OI FLOW / VELOCITY     │ D-08 FII / DII                │
└─────────────────────────────┴────────────────────────────────┘

INSTITUTIONAL
┌──────────────────┬──────────────────┬────────────────────────┐
│ D-09 Regime      │ D-10 Footprint   │ D-11 Concentration     │
└──────────────────┴──────────────────┴────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ D-12 ACTIVITY CRUX                                           │
└──────────────────────────────────────────────────────────────┘

CONFIRMATION — collapsed by default
┌──────────────────────────────────────────────────────────────┐
│ D-13 → D-14 → D-15 → D-16 → D-17                            │
└──────────────────────────────────────────────────────────────┘
```

## 6.2 Compact — width < 1280px

All multi-column grids collapse to one column while preserving source order:

`D-02 → D-03 → D-04 → D-06 → D-07 → D-08 → D-09 → D-10 → D-11 → D-12 → Confirmation`

No card is promoted because of signal strength.

## 6.3 Height rules

- D-01 SHALL never share its row.
- D-05 is not mounted in the Dashboard layout; it is opened deliberately as a dedicated Tier-3 surface.
- Tier-3 additions SHALL NOT increase executive-card height.
- Cards in the same executive grid row SHOULD maintain compatible visual height.
- Confirmation expansion SHALL only increase the Confirmation zone's own height.

---

# 7. Card Contracts

## D-00 — Status Bar

**Question:** Is the data valid, and what am I viewing?

### Tier 1
- Symbol selector.
- Spot value.
- Immediate price direction.
- Point and/or percentage move.
- Expiry selector.
- DTE.
- Data timestamp / `As of`.
- Feed health.
- D-18 paper-fund pill.

### Ownership
D-00 owns **view context**, not analytical metrics.

### Rules
- Expiry selection here is global.
- No other dashboard card may introduce an independent expiry selector.
- A stale/disconnected state must be obvious before the user reads D-01.

---

## D-01 — Decision Engine

**Question:** What should I do right now, and how sure is the system?

### Tier 1
- Directional bias / action.
- Confidence.
- Trade grade.
- Compact evidence strip containing read-only canonical references such as PCR, VIX, Max Pain and key wall/level indicators.

### Tier 3
- Trap warning.
- Active signals.
- Support/resistance detail.
- Strategy name/rationale.
- Evidence contribution breakdown when available.

### Rules
- D-01 SHALL consume canonical metrics; it SHALL NOT recompute display duplicates.
- Confidence must be visually distinct from directional bias.
- Missing critical inputs must degrade confidence/state rather than silently displaying an apparently complete call.
- A strong animation or flashing effect is prohibited as a method of communicating ordinary confidence changes.

---

## D-02 — Market Health & Story

**Question:** What coherent market story explains the current state?

### Tier 1
One concise regime/story headline.

### Tier 2
Short explanatory narrative synthesizing positioning, price and flow.

### Rules
- Narrative must explain; it must not become a second Decision Engine.
- Avoid repeating every metric numerically.
- Where values are mentioned, consume canonical values.

---

## D-03 — Greeks / Net GEX Alerts

**Question:** What gamma/Greek conditions materially affect the trade?

### Tier 1
- Gamma regime.
- Live Gamma Flip.
- Net GEX directional state.
- Material Greek alert(s).

### Tier 2
Compact explanation of why current gamma conditions matter.

### Tier 3
Full per-strike Delta/Gamma/Theta/Vega and GEX detail.

### Canonical ownership
- Live Net GEX.
- Live Gamma Flip.
- Dashboard Greek alert state.

---

## D-04 — Option Chain Snapshot

**Question:** What does aggregate option positioning say?

### Tier 1
- Aggregate/Range PCR as defined by analytics contract.
- Max Pain.
- OI balance/summary.

### Tier 2
- Range PCR for selected ±N chain window.
- ΔOI shift.
- Vol/OI context.

### Canonical ownership
- Dashboard PCR family.
- Max Pain.
- Aggregate option-chain positioning summary.

---

## D-05 — Option Chain

**Question:** What is happening at each strike?

### Surface model

D-05 is a **dedicated Tier-3 Option Chain surface**, not an always-mounted Dashboard card.

The Dashboard entry point is D-04 (Option Chain Snapshot). D-04's header opens D-05 while preserving the active trading context.

### Required behavior

- Receive current symbol from Dashboard/global state.
- Receive current expiry from D-00/global state.
- Receive current range where applicable.
- Preserve live synchronization with the Dashboard.
- ATM auto-centred on initial/context-changing render.
- Independent scrolling within the Option Chain page/surface.
- Scroll position survives live ticks.
- Clicking a strike toggles inline Greek detail where supported.
- Cross-report drill-down may open/focus D-05 at a specific strike.
- A targeted strike may be temporarily highlighted.
- Highlighting must not mutate analytics.

### Ownership

D-05 owns authoritative **per-strike display primitives**, including the per-strike institutional-score primitive where applicable.

Whole-chain interpretations remain owned by their analytical Dashboard cards.

### Rationale

Keeping the dense chain as a dedicated surface avoids duplicating a large strike table inside the executive Dashboard while preserving a direct analytical path:

```text
Dashboard D-04 Snapshot
        ↓ header / drill-down
D-05 Dedicated Option Chain
        ↓ strike selection
Strike Detail / deeper analysis
```

This is consistent with the three-tier information model: executive summary on the Dashboard, dense exploration in Tier 3.

## D-06 — Greeks by Moneyness

**Question:** How does Greek exposure change across moneyness?

### Tier 2
Delta, Gamma, absolute Theta and Vega across strikes.

### Rules
- Chart uses the same Greek source as D-03/D-05.
- It is a visualization consumer, not an independent calculator.
- A tick that does not change chart inputs SHALL NOT redraw the chart.

---

## D-07 — Vol/OI Velocity + OI Flow

**Question:** Where is fresh derivatives activity accelerating?

### Tier 1
- Dominant OI-flow direction.
- Highest-significance velocity/block-print signal.

### Tier 2
- OI build/unwind summary.
- Vol/OI velocity.
- Important strike/side concentration.

### Tier 3
Expanded flow report.

### Rules
- Static OI concentration must not be presented as fresh flow.
- If a strike is surfaced, it may drill down to D-05 via in-page strike highlighting.

---

## D-08 — FII/DII Summary

**Question:** What does institutional cash-market flow contribute?

### Tier 1
Net institutional flow posture.

### Tier 2
FII/DII decomposition and explanatory context.

### Tier 3
Expanded institutional cash-flow report.

### Rules
Cash-market data must be clearly distinguished from derivatives positioning.

---

## D-09 — Market Regime & Smart Money

**Question:** What regime and smart-money posture best describe the market?

### Tier 2
- Market regime.
- Whole-market smart-money interpretation.
- Regime confidence/context.

### Ownership
Owns the **whole-chain Smart Money interpretation**, not the per-strike primitive.

---

## D-10 — Institutional Footprint Score

**Question:** How strong is institutionally significant activity?

### Tier 2
- Footprint score.
- Strength label.
- Primary contributors.

### Rules
Score is an analytical interpretation and SHALL not masquerade as directly observed participant identity.

---

## D-11 — Capital Concentration

**Question:** Where is meaningful capital concentrated?

### Tier 2
- Capital walls/concentration.
- Dominant call/put capital areas.
- Relevant near-spot context.

### Ownership
Owns dashboard capital-concentration interpretation.

---

## D-12 — Institutional Activity Crux

**Question:** Which near-ATM strikes deserve investigation?

### Tier 2
Compact near-ATM ledger.

### Tier 3
Strike Detail Report entry.

### Rules
- D-12 summarizes; D-05 remains the strike table.
- It SHALL not duplicate the entire option chain.
- Selecting a strike may open its Tier-3 strike report or use the explicitly defined in-page drill-down behavior.

---

## D-13 — Volatility

**Question:** Does volatility context support the decision?

### Tier 2–3
- IV Rank / volatility context.
- Relevant volatility interpretation.

---

## D-14 — Probability

**Question:** Which chain outcomes rank highest probabilistically?

### Tier 2–3
Smart Money Ranking / whole-chain ranking, top 8 by absolute ΔOI where that remains the approved ranking contract.

---

## D-15 — Scenario Analysis

**Question:** How does the thesis behave if market conditions change?

### Tier 3
Scenario P&L and scenario-adjusted analytics.

### Naming rule
Scenario-derived metrics must be explicitly qualified and never presented under the same unqualified label as their live counterparts.

---

## D-16 — Advanced Analytics

**Question:** What specialist evidence is available for deliberate inspection?

### Tier 3
Collapsed by default. May contain:
- GEX table.
- OI Velocity.
- Per-strike Greeks.
- Capital/Futures Confirmation.
- Conviction Gauge.

This grouping is temporary architecture and may be decomposed by a future PDS revision.

---

## D-17 — Strategy Simulator

**Question:** How do active strategies behave under simulated conditions?

### Tier 3
Conditional card, rendered only when open strategies exist.

Includes:
- Strategy payoff.
- Institutional F&O simulator where applicable.

---

## D-18 — Paper Trading

Persistent surface.

- Compact fund pill belongs in D-00.
- Full paper-trading panel is not a dashboard zone card.
- Paper account state SHALL not alter analytical metric ownership.

---

## D-19 — Price Chart

Persistent dedicated tab/surface.

The Dashboard owns only a live-sync/navigation link. The chart does not become a dashboard card.

---

# 8. Metric Ownership Contract

The table below defines display ownership. Domain calculation ownership remains with the appropriate analytics module.

| Metric / interpretation | Canonical dashboard owner | Allowed consumers |
|---|---|---|
| Spot / view context | D-00 | All |
| Expiry / DTE | D-00 | All |
| Decision bias | D-01 | Supporting surfaces may reference |
| Decision confidence | D-01 | Supporting surfaces may reference |
| Trade grade | D-01 | Supporting surfaces may reference |
| PCR family | D-04 | D-01, D-02, D-14 |
| Max Pain | D-04 | D-01, D-02 |
| Live Net GEX | D-03 | D-01, D-02, D-09, D-16 |
| Live Gamma Flip | D-03 | D-01, D-02 |
| Per-strike Greeks | D-05 / shared Greek source | D-03, D-06, D-16 |
| Whole-chain Smart Money interpretation | D-09 | D-01, D-02, D-14 |
| Per-strike institutional primitive | D-05 | D-10, D-12 |
| Capital concentration / walls | D-11 | D-01, D-02, D-12 |
| OI-flow interpretation | D-07 | D-01, D-02, D-09 |
| FII/DII cash-flow interpretation | D-08 | D-01, D-02, D-09 |
| Volatility confirmation | D-13 | D-01 where required |
| Scenario-adjusted metrics | D-15 | Scenario surfaces only |

### 8.1 Ownership invariants

1. A consumer receives a canonical value or derived object; it does not duplicate the computation.
2. The same label at the same timestamp SHALL not show conflicting values.
3. A consumer may format a value differently, but SHALL preserve semantic identity.
4. Scenario/historical values SHALL carry explicit qualifiers.
5. If canonical data is unavailable, consumers SHALL show unavailable/stale state rather than substitute a local approximation without labeling it.

---

# 9. Dependency Architecture

Logical dependency direction:

```text
RAW / NORMALIZED MARKET DATA
            │
            ▼
      DOMAIN ANALYTICS
            │
     ┌──────┼───────────────┐
     ▼      ▼               ▼
 Position  Greeks       Capital/Flow
     │      │               │
     └──────┼───────────────┘
            ▼
    CANONICAL METRIC STORE
            │
     ┌──────┼───────────────┐
     ▼      ▼               ▼
   D-03    D-04           D-07..D-13
     └──────┼───────────────┘
            ▼
        D-01 DECISION
            │
            ▼
       UI PRESENTATION
```

### 9.1 Dependency rules

- UI cards SHALL NOT call each other to obtain analytical values.
- Cards consume shared canonical state.
- D-01 may depend on metrics owned for display by other cards, but not on those cards' DOM.
- Removing a card from the DOM SHALL not break the underlying decision computation.
- A collapsed Confirmation zone SHALL continue receiving state updates.

---

# 10. Data and Rendering Contract

## 10.1 Pipeline

```text
Live Tick / Snapshot
        ↓
Normalize / Validate
        ↓
Canonical Raw State
        ↓
Derived Metrics
        ↓
Decision State
        ↓
Change Detection
        ↓
Affected Component Patches
        ↓
DOM / Canvas
```

## 10.2 Rules

1. No tick SHALL trigger a full-page rerender.
2. Only affected components SHALL patch.
3. A component SHALL not mutate another component's DOM as a substitute for shared state.
4. Expensive chart redraws SHALL be invalidated by relevant data changes, not by every tick.
5. User interaction state and market-data state SHALL remain logically separate.
6. Data updates received while a modal is open SHALL update underlying state without closing/reopening the modal.
7. D-05 scroll state SHALL survive updates.
8. Confirmation receives fresh data while collapsed.

---

# 11. UI State Model

Every data-bearing card supports the following conceptual states where applicable:

```text
INITIAL
   ↓
LOADING
   ↓
LIVE ────────────────┐
   │                 │
   ├──→ PARTIAL      │
   │      │          │
   ├──→ STALE        │
   │      │          │
   └──→ DISCONNECTED │
          │          │
          └──→ RECOVERING
                    │
                    └──→ LIVE
```

## 11.1 Loading

- Structural shell may render.
- No fabricated zero values.
- Skeleton/placeholder treatment follows PDS-00.
- Loading SHALL not be confused with `0`.

## 11.2 Live

All critical inputs are within freshness tolerance.

## 11.3 Partial

Some non-critical inputs are unavailable.

- Available metrics may continue displaying.
- A visible partial-data indication is required where interpretation may be affected.
- D-01 must downgrade/qualify confidence if missing inputs materially affect the decision.

## 11.4 Stale

Last valid data exists but freshness tolerance has been exceeded.

- Values may remain visible.
- Stale state must be visibly identified.
- Stale values SHALL not visually resemble current live values without qualification.

## 11.5 Disconnected

Live source unavailable.

- Preserve last valid snapshot where useful.
- Clearly mark feed disconnection.
- Do not continuously clear/repopulate cards.

## 11.6 Recovering

Connection restored but complete canonical state is not yet validated.

The UI SHALL avoid declaring `Live` until the required snapshot/state is coherent.

---

# 12. Error and Exceptional States

| Situation | Required behavior |
|---|---|
| Initial load | Skeleton/placeholder; no false zeros |
| API timeout | Preserve last valid state when available; mark stale/error |
| WebSocket disconnect | D-00 visibly reports disconnection; cards preserve last valid values |
| Partial payload | Patch valid fields; flag affected interpretation |
| Invalid metric | Display unavailable, never `NaN`, `undefined`, or misleading zero |
| Market holiday | Show non-live/market-closed context without treating it as system failure |
| No open strategies | D-17 does not render |
| Empty FII/DII source | D-08 shows unavailable/empty state, not neutral flow |
| Missing Greeks | D-03/D-06 degrade explicitly; D-01 confidence handles missing dependency |
| Recovery | Reconcile canonical snapshot before resuming normal live state |

Errors SHALL be localized where possible. One failed card must not blank the page.

---

# 13. Navigation Architecture

## 13.1 Global expiry

D-00 owns the sole Dashboard expiry selector.

Changing expiry SHALL update every expiry-dependent metric through shared state.

## 13.2 Header-as-navigation

Cards with Tier-3 detail use their header as the primary open affordance:

- D-03
- D-04 → opens D-05 dedicated Option Chain
- D-07
- D-08
- D-12

No redundant `Open`, `View`, `Full Chain`, or `Details` button is required.

## 13.3 Modal contract

Every modal SHALL close through:

- explicit close control;
- click outside, where safe;
- `Esc`.

Focus SHALL return to the invoking control/header after close.

## 13.4 Strike drill-down

A surfaced strike/level from Decision, Capital Flow, Capital Concentration, or Institutional Activity may open/focus the dedicated D-05 Option Chain surface at that strike.

The semantic action SHOULD be equivalent to:

```text
openOptionChainAtStrike(strike, context)
```

It SHALL:

1. preserve symbol;
2. preserve expiry;
3. preserve range where applicable;
4. open/focus D-05;
5. scroll the target strike into view;
6. apply a temporary highlight;
7. preserve the user's ability to continue chain inspection.

This is a cross-surface drill-down, not a duplicate Dashboard table.

## 13.5 Section jump

A future mini-nav may jump only to defined zone boundaries. It SHALL not create a second report taxonomy.

---

# 14. Interaction Concurrency

Live ticks SHALL never visibly interrupt an active gesture.

Examples:

- A slider being dragged keeps user control.
- A `<details>` element being toggled is not forced back by a render.
- A modal remains open through ticks.
- Option-chain scroll is not reset.
- Inline strike expansion is not collapsed because the row received new data.

### Priority rule

```text
Active User Gesture
        >
Transient UI Animation
        >
Incoming Visual Patch
```

The data itself may update underneath the interaction; only disruptive presentation is deferred/coalesced.

---

# 15. Accessibility Contract

All interactive Dashboard functionality SHALL be operable without a mouse.

## 15.1 Keyboard

- `Tab` / `Shift+Tab`: logical focus traversal.
- `Enter` or `Space`: activate clickable card headers/controls as appropriate.
- `Esc`: close modal.
- Focus SHALL never be lost to `body` after modal close.
- D-05 row/strike interaction SHALL expose an accessible keyboard equivalent.

## 15.2 Focus

Every interactive element SHALL have a visible focus state distinct from hover.

## 15.3 Semantics

- Header-as-click-target SHALL use appropriate interactive semantics.
- Status shall not be communicated by color alone.
- Icons with meaning require accessible labels/text.
- Decorative icons shall not create redundant announcements.

## 15.4 Motion

Critical information SHALL remain understandable with reduced motion enabled.

---

# 16. Animation Contract

Animation exists to communicate state change, not decorate live data.

Default behavior:

- Subtle hover transition.
- Short modal open/close transition.
- Temporary strike-highlight transition.
- No bouncing/pulsing for ordinary metric changes.
- No repeated flashing on every live tick.
- No layout-shifting count-up animation for frequently updating numbers.

Recommended interaction transitions follow PDS-00 and SHOULD remain approximately 150–250 ms unless a component has a documented reason otherwise.

---

# 17. Responsiveness

## Desktop ≥ 1280px

Use the exact grids defined in §6.

## Compact < 1280px

- Multi-column rows become single-column.
- Card order remains fixed.
- No content is removed merely because the viewport is compact.
- Confirmation remains collapsed by default.
- D-05 retains its own horizontal/vertical usability strategy without forcing the whole page to adopt table width.

A phone-specific layout is not defined in v1.1.

---

# 18. Performance Contract

## 18.1 Update isolation

A slow card may degrade its own freshness but SHALL NOT synchronously block all other card updates.

## 18.2 DOM

- Patch changed nodes.
- Avoid replacing entire card roots for simple numeric updates.
- Preserve user-controlled DOM state.

## 18.3 Charts

A chart redraw requires a relevant input change.

Resize/redraw caused solely by an unrelated tick is prohibited.

## 18.4 Option Chain

- Preserve scroll.
- Avoid rebuilding all rows where targeted updates are sufficient.
- ATM auto-centering occurs on initial/context-changing render, not on every tick.

## 18.5 Collapsed content

Collapsed content receives state updates but expensive rendering work MAY be deferred until visible when doing so does not make the opened view stale.

---

# 19. Decision Confidence Contract

D-01 confidence represents the system's **degree of agreement/support for the current decision**, not certainty of profit.

The exact mathematical formula belongs to the Decision Engine specification, but the Dashboard contract requires that confidence:

- derives from approved canonical evidence;
- accounts for unavailable critical inputs;
- does not double-count the same underlying signal merely because it appears in multiple cards;
- is timestamp-coherent with the decision;
- can expose contribution detail in Tier 3;
- never implies guaranteed outcome.

Conceptually:

```text
Positioning ─┐
Gamma ───────┤
Capital Flow ├──→ Evidence / Decision Engine ──→ Bias + Confidence
Institutional┤
Volatility ──┘
```

The UI SHALL not implement its own confidence formula.

---

# 20. Visual Priority Contract

Visual weight SHALL follow:

```text
D-01 Decision Engine
        >
Structure / Capital Flow
        >
Institutional
        >
Confirmation
```

The following SHALL NOT overpower D-01:

- zone headings;
- large decorative charts;
- alert colors;
- modal-launch affordances;
- animated live values.

Color intensity SHALL correspond to semantic importance, not merely data magnitude.

---

# 21. Consistency Invariants

At all times:

1. One expiry context governs the Dashboard.
2. One canonical value exists for each shared metric at a given state version.
3. Card order does not depend on market values.
4. Closing/opening Tier-3 detail does not alter analytics.
5. Live ticks do not reset user navigation state.
6. A hidden/collapsed card does not become analytically stale solely because it is hidden.
7. Scenario metrics cannot masquerade as live metrics.
8. `0`, unavailable, loading, stale and disconnected are distinct states.
9. Display formatting cannot change metric meaning.
10. Decision Engine remains the primary visual and semantic output.

---

# 22. Implementation Boundary

A senior frontend engineer is free to choose internal implementation patterns provided these contracts hold.

Recommended conceptual separation:

```text
Dashboard Context
├── Market / Feed State
├── View State
│   ├── symbol
│   ├── expiry
│   └── freshness
├── Canonical Metrics
├── Decision State
└── Interaction State
    ├── open modal
    ├── expanded strike
    ├── confirmation collapse
    └── temporary highlight
```

Market data SHALL not directly overwrite interaction state.

The architecture SHOULD make canonical metrics consumable independently of whether their owner card is currently mounted or visible.

---

# 23. Acceptance Criteria

Dashboard v1.1 is accepted when all of the following are demonstrably true:

1. Status + Decision Engine alone provide an actionable first-glance answer.
2. All Dashboard cards are reachable in prescribed zone order by scrolling; D-05 is reachable from D-04 and defined strike drill-downs as a dedicated Tier-3 surface.
3. D-01 occupies a full-width dedicated row.
4. D-02/D-03/D-04 are 3-up on desktop and ordered single-column in Compact.
5. D-07/D-08 are 2-up on desktop.
6. D-09/D-10/D-11 are 3-up, followed by full-width D-12.
7. Confirmation is collapsed by default.
8. D-17 renders only when its precondition exists.
9. D-00 is the only Dashboard expiry control.
10. Changing expiry updates all expiry-dependent consumers.
11. PCR cannot show two conflicting current values under the same semantic label.
12. Max Pain cannot show conflicting current values.
13. Live Net GEX and Gamma Flip have D-03 as display owner.
14. Scenario-adjusted metrics are explicitly qualified.
15. A live tick does not trigger a full-page rerender.
16. D-05 scroll position survives live ticks.
17. D-05 ATM auto-centering occurs only on initial/context-changing render and does not repeatedly override manual scrolling.
18. A modal remains open during incoming ticks.
19. `Esc` closes every Dashboard modal.
20. Modal focus returns to its invoker.
21. A strike drill-down opens/focuses D-05 and scrolls to/highlights the correct strike.
22. A clicked D-05 strike can expose its inline Greek detail within the dedicated Option Chain surface.
23. Collapsed Confirmation receives fresh underlying state.
24. A chart with unchanged relevant inputs does not visibly redraw.
25. A missing value never appears as `NaN`, `undefined`, or fabricated zero.
26. Stale data is visibly distinguishable from live data.
27. Feed disconnection is visible in D-00.
28. Partial critical data appropriately qualifies/degrades D-01 confidence.
29. One card's data failure does not blank unrelated cards.
30. All interactive elements expose hover and keyboard focus.
31. Clickable headers are keyboard operable.
32. Status is not communicated by color alone.
33. Crossing the 1280px breakpoint neither duplicates nor removes cards.
34. Runtime market values never reorder cards.
35. Confirmation expansion does not reorder content outside its zone.
36. Ordinary live updates do not use distracting flashing/bouncing.
37. Decision Engine remains visually dominant.
38. Removing/hiding a metric-owner card does not destroy canonical metric state required by other consumers.
39. No UI card independently re-derives a canonical shared metric merely for display.
40. The implementation can be reviewed against this document without requiring undocumented product decisions.

---

# 24. Verification Checklist

Before release, engineering/QA SHOULD verify:

### Information architecture
- [ ] Correct zone order.
- [ ] Correct card order.
- [ ] Correct desktop grid.
- [ ] Correct Compact stack.
- [ ] D-05 is not duplicated inside Dashboard.

### Ownership
- [ ] Shared metric consumers use canonical state.
- [ ] No duplicate calculation paths introduced in UI.
- [ ] Scenario labels are qualified.

### Interaction
- [ ] Modal close paths work.
- [ ] Keyboard operation works.
- [ ] D-04 header opens D-05 with current context.
- [ ] Strike drill-down opens/focuses D-05 at the requested strike.
- [ ] Scroll/expand state survives ticks.

### Feed states
- [ ] Loading tested.
- [ ] Partial tested.
- [ ] Stale tested.
- [ ] Disconnect/recovery tested.
- [ ] Market-closed state tested.

### Performance
- [ ] No full-page tick rerender.
- [ ] Chart invalidation verified.
- [ ] Option-chain scroll stability verified.
- [ ] Slow-card isolation verified.

---

# 25. Future Extension Points

The following are explicitly deferred and require a PDS revision:

- Declarative/data-driven dashboard configuration.
- Cross-report drill-down beyond the defined D-05 strike-focus/detail routes.
- Adaptive collapse defaults based on viewport.
- Phone-specific layout.
- Persisted collapse state across sessions.
- Mandatory section-jump mini-nav.
- Decomposition of D-16 Advanced Analytics.
- User-configurable card order.
- User-customizable zones.
- Historical replay mode.
- Multi-expiry comparative Dashboard.

---

# 26. Change Control

Any change that does one or more of the following requires a PDS version update:

- adds/removes a card;
- changes zone membership or dedicated-surface placement;
- changes canonical metric ownership;
- changes default collapse state;
- changes cross-report navigation;
- introduces another global context control;
- changes Decision Engine Tier-1 semantics;
- changes the defined breakpoint model.

Pure implementation refactors that preserve all observable contracts do not require a PDS revision.

---

# 27. Final Architecture Summary

```text
                    mTERMINALS DASHBOARD

                ┌─────────────────────┐
                │ D-00 STATUS/CONTEXT │
                └──────────┬──────────┘
                           ▼
                ┌─────────────────────┐
                │ D-01 DECISION       │
                │ Bias + Confidence   │
                └──────────┬──────────┘
                           ▼
        ┌─────────────────────────────────────┐
        │ STRUCTURE & POSITIONING             │
        │ D-02 │ D-03 │ D-04 → D-06          │
        └──────────────────┬──────────────────┘
                           ▼
        ┌─────────────────────────────────────┐
        │ CAPITAL FLOW                        │
        │ D-07                  │ D-08        │
        └──────────────────┬──────────────────┘
                           ▼
        ┌─────────────────────────────────────┐
        │ INSTITUTIONAL                       │
        │ D-09 │ D-10 │ D-11 → D-12          │
        └──────────────────┬──────────────────┘
                           ▼
        ┌─────────────────────────────────────┐
        │ CONFIRMATION — collapsed            │
        │ D-13 → D-14 → D-15 → D-16 → D-17  │
        └─────────────────────────────────────┘

Canonical State → Derived Metrics → Decision → Targeted UI Patches

Dedicated Tier-3 surface:
D-04 → D-05 Option Chain → Strike Detail

Persistent surfaces:
D-18 Paper Trading     D-19 Price Chart
```

---

## Approval

**PDS-01 v1.1 is implementation-complete at the product/interaction architecture level.**

Detailed calculation formulas belong in the corresponding analytical/Decision Engine specifications; exact visual tokens belong in PDS-00. Neither should be duplicated here.
