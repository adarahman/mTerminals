# mTerminals — PDS-01 Dashboard Implementation Audit

| Field | Value |
|---|---|
| **Audit baseline** | `mterminals(6).zip` |
| **Specification** | `docs/01_Product_Architecture/PDS-01_Dashboard.md` v1.1 |
| **Audit date** | 2026-08-07 |
| **Scope** | Dashboard frontend + shared live-state/rendering paths that materially affect PDS-01 |
| **Result** | **Partially compliant — IA largely implemented; several P0 contracts remain** |

---

## Audit correction — full project trace

A second full-tree trace confirmed that the dense Option Chain implementation is
present and actively synchronized. The earlier phrase **“D-05 missing”** was
incorrect if read as “the code/files do not exist.”

The accurate finding is: **D-05 exists as a dedicated Option Chain surface,
while PDS-01 v1.1 currently specifies it as an always-mounted Dashboard card.**
This audit version corrects that distinction.

## 1. Executive Result

The current Dashboard is **much closer to PDS-01 than the older layout**. The major zone reorganization has already been implemented:

- Decision first.
- Structure & Positioning zone.
- Capital Flow zone.
- Institutional zone.
- Confirmation zone.
- Confirmation cards collapsed by default.
- Single Dashboard expiry selector.
- Header-as-navigation on several cards.
- Incremental live rendering for ordinary ticks.
- Central `MarketStore`, `WSManager`, `AppState`, and `PanelManager` foundations.

However, the implementation is **not yet PDS-01 complete**.

The most important gaps are:

1. **D-05 is fully implemented, but as a dedicated Option Chain surface rather than an always-mounted Dashboard card.** This is an implementation-vs-PDS placement mismatch, not a missing-file problem.
2. **D-00 does not expose persistent feed health/stale/disconnected state** after the loader disappears; stale decisions can remain visually indistinguishable from live ones.
3. **The 1280px responsive contract is not implemented**; current breakpoints are mainly 900px and several grids stay multi-column.
4. **D-04 Option Chain Snapshot still contains OI Flow and Capital Flow**, violating one-question-per-card and zone ownership.
5. **Shared-metric ownership is not fully aligned**: Max Pain and capital walls are shown/derived outside their specified canonical display owners.
6. **The PDS strike-drill-down contract must be reconciled with the dedicated Option Chain architecture** rather than assuming a missing Dashboard table.
7. **Modal accessibility is incomplete**: no focus trap/restore, and FII/DII lacks the same backdrop-close wiring as the others.
8. **Several click-only `<div>` chart affordances are not keyboard-operable**.
9. **Whole-card `outerHTML` replacement remains common on ticks**, and the Greeks-by-Moneyness chart redraws on every tick even when relevant inputs do not change.

### Overall grading

| Area | Grade |
|---|---|
| Information architecture / zone order | **A-** |
| Card inventory compliance | **B-** |
| Metric ownership | **C+** |
| Navigation / drill-down | **C+** |
| Live-state correctness | **C** |
| Rendering / performance | **B-** |
| Responsiveness | **D** |
| Accessibility / modal contract | **C** |
| Overall PDS-01 compliance | **~72%** |

---

# 2. Priority Definitions

| Priority | Meaning |
|---|---|
| **P0** | PDS contract materially broken; can mislead the trader or blocks required workflow |
| **P1** | Important architecture/UX/performance non-compliance; should be corrected in the current alignment pass |
| **P2** | Cleanup, consistency, maintainability or polish |

---

# 3. P0 Findings

## P0-01 — D-05 placement differs from PDS-01; implementation itself is present

### Correction after full-project trace

The full Option Chain implementation is **not missing**.

It exists at:

- `frontend/OptionChain/option-chain.html`
- `frontend/OptionChain/option-chain.js`
- `frontend/OptionChain/option-chain.css`

The page contains the dense `#ocTable` / `#ocBody` strike ledger and performs
per-row diffing rather than rebuilding the entire `<tbody>` on every tick.

The Dashboard-side integration also exists:

- `frontend/Dashboard/chain/chain-sync.js`
- `frontend/Dashboard/chain/chain-depth.js`
- `frontend/Dashboard/chain/chain-view.js`
- `frontend/Dashboard/dashboard-panels.js`
- `frontend/Dashboard/dashboard.js`

`ChainDenseView` keeps the dedicated Option Chain synchronized through
`BroadcastChannel("oc-live-sync")`, including snapshot, expiry, range, Greeks,
Max Pain, Vol/OI ratios and paper-order routing.

### What is actually different

`DashboardPro.html` deliberately removed the former static `#sec-chain` block.
`buildChainSummaryHtml()` now exposes the Option Chain through the D-04 header
link to `../OptionChain/option-chain.html`.

Therefore the mismatch is:

```text
PDS-01 v1.1
D-05 = full-width, always-mounted Dashboard card

CURRENT IMPLEMENTATION
D-05-equivalent dense ledger = dedicated OptionChain surface
D-04 header = navigation entry
Dashboard = compact executive snapshot
```

### Legacy/orphaned path discovered

`frontend/Dashboard/strike-detail-report-panel.js` still defines
`toggleFullChainFocus()`, which can inject the same `option-chain.html` page as
an inline iframe.

However it requires:

```text
#full-chain-toggle-btn
```

and no current Dashboard template creates that element. The active D-04 header
is an `<a>` link instead. The function is therefore currently unreachable
legacy code.

`PROJECT-ARCHITECTURE.md` still describes this inline-iframe path, so that
particular documentation statement is stale relative to the current source.

### Architecture recommendation

**Do not duplicate the dense chain back into the Dashboard unless there is a
strong workflow reason.**

The current dedicated-surface architecture is actually consistent with the
Dashboard philosophy:

- executive information remains compact;
- deep strike analysis is deliberate;
- one dense Option Chain implementation is maintained;
- the dedicated page already receives the same live state.

Recommended PDS revision:

```text
D-04 Option Chain Snapshot
Tier 1–2 Dashboard card
Header opens dedicated Option Chain surface

D-05 Option Chain
Tier 3 / dedicated surface
Not an always-mounted Dashboard zone card
Receives symbol/expiry/range/live state from the Dashboard
```

If this PDS revision is accepted, this item becomes **COMPLIANT**, not P0.

If PDS-01's current full-width Dashboard D-05 requirement is retained unchanged,
then the implementation is non-compliant by placement only.

## P0-02 — Feed health is not persistently visible in D-00

### PDS contract

D-00 must answer:

> Is the feed current and what am I viewing?

It owns visible feed health, context and freshness.

### Current implementation

`DataService` only changes `#ws-status` red/green on socket open/close:

- `frontend/shared/services/data-service.js` ~32–38.

But `#ws-status` lives inside `#loader`:

- `frontend/Dashboard/DashboardPro.html` ~565–579.

After first successful render, `parseAndRender()` hides the loader:

- `frontend/shared/services/data-service.js` ~350–354.

The persistent top bar (`renderTopBarHtml`) shows symbol, spot, expiry, DTE and As-of, but **no feed-health state**.

There is also no first-class stale timeout/state machine in `MarketStore`/`WSManager`; socket close does not mark the displayed canonical state stale.

### Risk

A disconnected feed can leave the last Decision Engine values visible with no persistent indication that they are no longer live.

This is a decision-safety issue, not merely a cosmetic one.

### Required change

Add a persistent D-00 feed-status pill:

```text
LIVE
STALE 12s
DISCONNECTED
RECOVERING
PARTIAL
MARKET CLOSED
```

Recommended state ownership:

```text
WSManager transport events
        ↓
FeedState / freshness controller
        ↓
D-00 status pill
        ↓
D-01 degraded/stale qualification
```

The last valid analytical state may remain visible, but its freshness SHALL be explicit.

### Files

- `frontend/shared/services/ws-manager.js`
- `frontend/shared/services/data-service.js`
- `frontend/shared/stores/market-store.js`
- `frontend/shared/state/app-state.js`
- `frontend/Dashboard/chain/chain-template.js`
- `frontend/styles/navigation.css` / `components.css`

---

## P0-03 — Responsive breakpoint contract is incorrect

### PDS contract

- Desktop: `>=1280px`.
- Compact: `<1280px`.
- Every multi-column Dashboard grid collapses to **one column** in Compact.

### Current implementation

`frontend/styles/responsive.css`:

- `.exec-grid` changes only at `max-width:900px`.
- At that breakpoint it becomes **2 columns**, not one.
- `.row2/.row3` become one column only below 900px.
- Navigation changes below 900px.

`frontend/styles/layout.css`:

- `.exec-grid` defaults to 3 columns.

Capital Flow is rendered with an **inline** two-column style in
`chain-renderer.js`:

```html
display:grid;
grid-template-columns:1fr 1fr;
```

which has no PDS-compliant `<1280` one-column contract.

### Consequence

At widths such as 1000px, 1100px or 1200px the implementation remains in a desktop-like multi-column layout even though PDS-01 defines those widths as Compact.

### Required change

Introduce the authoritative breakpoint:

```css
@media (max-width:1279px) {
  .exec-grid,
  .capital-flow-grid,
  .institutional-grid,
  .row2,
  .row3 {
    grid-template-columns: 1fr;
  }
}
```

Remove inline grid definitions that prevent centralized responsive behavior.

### Files

- `frontend/styles/responsive.css`
- `frontend/styles/layout.css`
- `frontend/Dashboard/chain/chain-renderer.js`
- possibly `frontend/styles/panels.css`

---

## P0-04 — D-04 violates one-question-per-card and zone ownership

### PDS contract

D-04 Option Chain Snapshot owns:

- OI totals.
- Range PCR.
- ΔOI shift.
- Vol/OI.

Capital/OI **flow** belongs in D-07.

### Current implementation

`buildChainSummaryHtml()` currently contains:

1. OI Summary.
2. Chg OI Summary.
3. **OI Flow 5m/15m/30m**.
4. **Capital Flow CE ₹ / PE ₹ / Net Flow**.

Evidence: `frontend/Dashboard/chain/chain-template.js` ~583–764.

The same page also has the dedicated D-07 Capital Flow zone.

### Problem

D-04 is answering both:

- “What does aggregate option positioning say?”
- “Where is money moving intraday?”

This creates duplicated evidence and weakens the zone architecture.

### Required change

Keep D-04 to:

```text
OI totals
Range PCR
ΔOI shift
Vol/OI
Max Pain (canonical owner)
```

Move/remove from D-04:

```text
OI Flow 5/15/30m     → D-07
Capital Flow         → D-07 / D-11 as defined
```

---

## P0-05 — Max Pain canonical display ownership is inverted

### PDS contract

PDS-01 metric ownership:

- Max Pain canonical display owner: **D-04**.
- D-01 may show a read-only reference.

### Current implementation

D-01 prominently renders `d.maxPain`.

D-04 `buildChainSummaryHtml()` does **not** render Max Pain.

### Required change

D-04 must become the authoritative visible Max Pain display.

D-01 may continue to show Max Pain only as a labelled read-only reference to the same canonical field.

No second derivation.

---

## P0-06 — Reconcile strike drill-down with the dedicated Option Chain surface

### PDS contract

PDS-01 currently says a strike surfaced by Decision/Capital Flow should scroll
to and highlight D-05 inside the Dashboard.

### Current architecture

The dense chain is a dedicated `OptionChain/option-chain.html` surface, synced
from the Dashboard through `oc-live-sync`.

Therefore the correct implementation should not depend on a nonexistent
Dashboard `#sec-chain`.

### Recommended revised contract

Introduce one semantic navigation action:

```js
openOptionChainAtStrike(strike, options)
```

It should:

1. preserve current symbol;
2. preserve current expiry;
3. preserve current global range;
4. open/focus the dedicated Option Chain surface;
5. request/scroll to the target strike;
6. apply a temporary highlight;
7. optionally expand Greeks only when explicitly requested.

Consumers:

- D-01 key wall/level.
- D-07 dominant flow strike.
- D-11 capital wall.
- D-12 institutional strike.

The existing `BroadcastChannel("oc-live-sync")` is a suitable transport for the
new `oc-focus-strike` message.

If the PDS is revised to make D-05 a dedicated Tier-3 surface, this satisfies the
same analytical workflow without duplicating the table in Dashboard.

# 4. P1 Findings

## P1-01 — D-00 is missing the Paper Trading fund pill

PDS-01 specifies D-18's fund pill in Status.

Current `chain-template.js` explicitly says the top-bar fund pill was removed
and moved into the Portfolio panel (~133–140).

### Recommendation

Restore only the compact non-invasive fund/P&L status pill to D-00.
Keep the full Portfolio panel separate.

---

## P1-02 — D-07 still has a separate OI Flow open button

PDS navigation rule:

> Tier-3 detail opens through the card header; no separate Open/View button.

The Vol/OI header follows this rule.

But the OI Flow portion of D-07 still has:

```html
<button class="oi-flow-open-btn" ...>
```

inside `buildChainSummaryHtml()`.

### Recommendation

When D-07 is fully separated from D-04, give the merged D-07 card one clear header affordance.
Do not keep a secondary icon-only “open” control that duplicates the same detail concept.

---

## P1-03 — Section-jump navigation violates zone-boundary rule

PDS permits an optional mini-nav only when it jumps to **zone boundaries**.

Current `#sec-nav-bar` jumps directly to cards such as:

- Decision.
- Greeks.
- FII/DII.
- Advanced.

It also mixes navigation with unrelated controls:

- Range/Velocity.
- Algo.
- Order.
- Portfolio.
- Backtest.

Evidence: `DashboardPro.html` ~481–522.

### Recommendation

Split concepts:

```text
Zone Jump
- Decision
- Structure
- Capital Flow
- Institutional
- Confirmation

Utility rail
- Range/Velocity
- Algo
- Order
- Portfolio
- Backtest
```

Or remove the jump layer entirely for v1.0, as PDS permits.

---

## P1-04 — Modal focus contract is incomplete

### What works

Most modals support:

- explicit close;
- Esc;
- backdrop click through inline handlers or dashboard listeners.

### Missing

`ModalManager._openModal()` only toggles classes.
It does not:

- store the invoker;
- focus the modal/first control;
- trap focus;
- restore focus on close.

### Additional inconsistency

FII/DII modal does not have the same explicit backdrop `onclick` in
`DashboardPro.html`, and the global backdrop listeners in `dashboard.js`
only wire OI and Greeks.

### Required change

Centralize all modal behavior in `ModalManager`:

```text
open(modal, invoker)
  save invoker
  make modal visible
  move focus inside
  activate focus trap

close(modal)
  hide
  remove trap
  restore invoker focus
```

No per-modal duplication.

---

## P1-05 — Clickable chart `<div>` elements are not keyboard-operable

Examples:

- Decision mini chart.
- Greeks-by-Moneyness chart.
- Strategy Payoff chart.
- Simulator GEX chart.

They use `onclick` on `<div>` with pointer cursor but no button semantics, keyboard activation or focus.

### Recommendation

Use `<button>` wrappers or add complete button semantics (`tabindex=0`, role, keyboard handler), preferably the former.

---

## P1-06 — Whole-card `outerHTML` replacement remains common on live ticks

The ordinary tick path is correctly **incremental**, which is a substantial improvement.

However `_rerenderChainPanels()` still calls `patchOuterHtmlIfChanged()` for entire cards including:

- D-04.
- D-07 summary.
- D-03.
- D-08.
- D-12.
- D-13–D-16.
- executive wrapper.

This avoids unnecessary writes when HTML is identical but still destroys/recreates the entire card when one displayed value changes.

Click guards and state-preservation code are compensating for this destructive granularity.

### Target

Progressively move hot cards toward:

```text
view model diff
  ↓
changed field IDs/data-bindings
  ↓
text/class/width patch
```

Keep full-card rebuild for structural change only.

---

## P1-07 — Greeks-by-Moneyness redraws on every tick

`_rerenderChainPanels()` unconditionally calls:

```js
updateGreeksMoneynessChart(_data)
```

`chart-legend.js` then updates all datasets and calls:

```js
chart.update('none')
```

for both inline and modal charts.

This removes animation but still redraws even if Greeks did not change.

### PDS requirement

A tick with no relevant chart-input change SHALL NOT redraw that chart.

### Recommendation

Compute a small chart signature from:

```text
atm + strikes + cDelta + cGamma + cTheta + cVega
```

and skip update when unchanged.

---

## P1-08 — D-02 is too broad and contains independent UI analytics

Current Market Health & Story includes:

- Momentum score.
- OI Flow score.
- Theta Burn score.
- Expected Move.
- ATM straddle premium.
- Engine Pick.
- Narrative.
- Top Movers.

It also computes `momScore`, `oiScore`, `thetaScore` directly in `exec-view.js`.

### PDS intent

D-02 is a narrative summary of current market state.

### Recommendation

Reduce it to:

- one primary market-state headline;
- 2–3 supporting explanations;
- optional compact health indicators that come from canonical analytics.

Move Top Movers elsewhere or make it Tier 3.
Avoid creating new decision-like scoring formulas in the presentation layer.

---

## P1-09 — D-08 semantic scope does not match its PDS question

PDS D-08:

> Institutional cash-market flow.

Current visible card is:

> FII / DII / Pro / Retail Sentiment

and combines participant OI/bias information, while the modal contains cash-flow panels.

### Recommendation

Choose one explicit product contract:

**Preferred PDS-aligned visible D-08:**
`FII/DII Cash Flow`

Then keep participant F&O decomposition in Tier 3.

Do not let cash market, participant derivative OI and composite sentiment share an unqualified label.

---

## P1-10 — D-12 is a summary, not the specified near-ATM ledger

PDS D-12 calls for a near-ATM ledger.

Current `buildInstitutionalActivitySummaryCard()` displays:

- near count;
- far count;
- bias;
- strongest signal.

It does not show the actual near-ATM ledger rows in the card.

### Recommendation

Show a compact 3–5 row ledger in D-12:

```text
Strike | Side | Footprint | OI/ΔOI | Capital/Flow cue
```

Keep the full Strike Detail Report in Tier 3.

---

## P1-11 — Capital-wall ownership is incomplete

PDS metric ownership assigns capital concentration/walls to D-11.

Current D-11 renders concentration percentage/top capital strikes.

D-01 separately renders:

- `capitalCeWallStrike`
- `capitalPeWallStrike`

D-11 does not clearly render those canonical wall values.

### Recommendation

Make D-11 visibly own:

- ₹ CE Wall.
- ₹ PE Wall.
- top concentration.
- concentration %.

D-01 may reference the same wall fields.

---

## P1-12 — Feed state lacks explicit Partial/Stale/Recovering model

`MarketStore` is a good canonical-state foundation, but it currently stores only merged market payload.

`WSManager` owns transport lifecycle, but there is no explicit state object for:

```text
INITIAL
CONNECTING
LIVE
PARTIAL
STALE
DISCONNECTED
RECOVERING
```

### Recommendation

Add:

```js
AppState.feed = {
  status,
  lastMessageAt,
  lastFullSnapshotAt,
  staleAfterMs,
  reason
}
```

or an equivalent dedicated store.

Do not mix this with analytical payload fields.

---

## P1-13 — Slow-card isolation is not yet architectural

`_rerenderChainPanels()` is a long synchronous pass.

Even though cards are patched separately, a slow chart/simulator calculation blocks later operations in the same JavaScript task.

### Recommendation

Split update phases:

```text
Critical
- D-00
- D-01

Visible normal
- D-02..D-12

Deferred/heavy
- hidden Confirmation
- modal-only charts
- simulator
```

Use rAF/idle/deferred scheduling only where it preserves freshness and interaction.

---

# 5. P2 Findings

## P2-01 — Stale comments contradict current D-05 reality

`chain-renderer.js` still contains comments describing a static persistent `#sec-chain` in `DashboardPro.html`.

`DashboardPro.html` explicitly says that block was removed.

This is a maintenance hazard because an engineer reading the renderer can believe D-05 still exists.

### Recommendation

After D-05 decision is implemented, clean all contradictory historical comments.

---

## P2-02 — Accidental `+` diff markers remain in DashboardPro.html

`DashboardPro.html` lines ~594–603 begin with literal `+` characters:

```html
+<!-- PRICE CHART MOUNT
+ ...
```

The first `+` is outside the HTML comment and can become a visible text node.

### Recommendation

Remove all literal diff markers.

---

## P2-03 — Price-chart mount/comments are obsolete

Dashboard startup currently comments out direct `priceChart.ensureMounted()` /
`hydrateRange()` calls and uses live-sync to the dedicated Price Chart surface.

However `#price-chart-mount` and comments describing an always-visible mini chart remain.

### Recommendation

After confirming no runtime consumer uses the mount node, remove dead mount markup/comments and avoid loading unused chart assets on Dashboard if they are no longer required.

This aligns with D-19: dedicated Price Chart surface, Dashboard link/sync only.

---

# 6. Card-by-Card Compliance Matrix

| ID | Card | Status | Notes |
|---|---|---|---|
| **D-00** | Status Bar | ⚠️ Partial | Symbol/spot/expiry/DTE/as-of good; missing persistent feed health and fund pill |
| **D-01** | Decision Engine | ✅/⚠️ Strong | Good Tier-1/Tier-3 split; some wall derivation/ownership should move to canonical source |
| **D-02** | Market Health & Story | ⚠️ Partial | Correct zone, but too broad and computes presentation-layer scores |
| **D-03** | Greeks / Net GEX Alerts | ✅/⚠️ Strong | Correct placement/header/modal; visible-range GEX/Gamma aggregation remains frontend-derived |
| **D-04** | Option Chain Snapshot | ❌ | Contains OI Flow + Capital Flow; missing Max Pain canonical display |
| **D-05** | Option Chain | ⚠️ Placement mismatch | Full implementation exists in `frontend/OptionChain/`; PDS currently says always-mounted Dashboard card |
| **D-06** | Greeks by Moneyness | ✅/⚠️ | Correct zone/full-width; click wrapper accessibility + redraw invalidation need work |
| **D-07** | Vol/OI Velocity + OI Flow | ⚠️ Partial | Merged presentation exists; OI Flow content is duplicated in D-04 and modal affordance not fully header-only |
| **D-08** | FII/DII Summary | ⚠️ Partial | Correct Capital zone; visible semantics broader than specified cash-market flow |
| **D-09** | Market Regime & Smart Money | ✅ | Correct zone and backend-fed analytics |
| **D-10** | Institutional Footprint Score | ✅ | Correct zone and backend-ranked data |
| **D-11** | Capital Concentration | ✅/⚠️ | Core card correct; should visibly own canonical capital walls |
| **D-12** | Institutional Activity Crux | ⚠️ Partial | Correct zone/header/detail link; summary instead of near-ATM ledger |
| **D-13** | Volatility | ✅ | Collapsed Confirmation card |
| **D-14** | Probability | ✅ | Collapsed Confirmation card |
| **D-15** | Scenario Analysis | ✅ | Collapsed and scenario-qualified |
| **D-16** | Advanced Analytics | ✅ | Collapsed and live-refreshed |
| **D-17** | Strategy Simulator | ✅/⚠️ | Conditional correctly; internal two-column layout/accessibility needs Compact cleanup |
| **D-18** | Paper Trading | ⚠️ Partial | Separate panels good; D-00 fund pill missing |
| **D-19** | Price Chart | ✅/P2 | Dedicated surface/live sync is correct; dead mount/comments should be cleaned |

---

# 7. PDS Acceptance Criteria Audit

| PDS acceptance criterion | Result |
|---|---|
| Status + Decision provides complete actionable answer | ⚠️ Feed freshness missing |
| Every card reachable in required zone order | ⚠️ D-05 exists as dedicated surface, not Dashboard-mounted |
| Shared metric cannot conflict | ⚠️ Ownership cleanup still required |
| Confirmation/modal interaction doesn't disturb other zones | ✅ Mostly |
| 1280px layout transition exactly as specified | ❌ |
| Tick during modal/drag/details causes zero disruption | ⚠️ Improved substantially; not fully guaranteed |
| Visual weight decreases downward | ✅ Mostly |
| D-00 sole Dashboard expiry control | ✅ |
| Expiry change updates dependent consumers | ✅ |
| D-05 scroll survives ticks | ✅ Dedicated Option Chain uses per-row updates; verify scroll behavior in runtime |
| Esc closes Dashboard modals | ✅ Mostly |
| Backdrop closes every modal | ⚠️ FII/DII inconsistent |
| Modal focus returns to invoker | ❌ |
| Strike drill-down reaches/highlights D-05 | ⚠️ Dedicated surface exists; explicit `focus strike` message/action still needed |
| Collapsed Confirmation remains current | ✅ |
| Unchanged chart inputs do not redraw | ❌ Greeks chart |
| Stale data visibly differs from live | ❌ |
| Feed disconnect visible in persistent D-00 | ❌ |
| Missing critical data degrades D-01 | ⚠️ Backend-dependent; no explicit frontend feed degradation |
| Runtime values never reorder cards | ✅ |
| No full-page rerender on ordinary live tick | ✅ |
| UI shared metrics are not independently re-derived | ⚠️ Several frontend derivations remain |

---

# 8. What Is Already Good and Should Not Be Rewritten

The following foundations are worth preserving:

## Central state

`MarketStore` already owns merged full/delta state.

Keep it.

## Transport separation

`WSManager` already owns WebSocket lifecycle only.

Keep it.

## View/application state

`AppState` has removed several accidental globals.

Continue that direction.

## Panel lifecycle

`PanelManager` / panel wrappers are a useful boundary.

Do not replace them with another framework merely for style.

## Tick scheduling

`DataService.scheduleRender()` already:

- coalesces multiple messages;
- avoids ordinary full Dashboard rebuilds;
- provides a timeout watchdog for background-tab rAF throttling.

Keep this architecture.

## Confirmation collapse preservation

D-13–D-16 preserve `<details>` open state on tick.

Keep this behavior.

## Header navigation

D-03, D-08, D-12 and Vol/OI header patterns are close to the desired standard.

Standardize around them.

---

# 9. Recommended Implementation Order

## Phase A — P0 Product correctness

### A1. Reconcile PDS D-05 with the existing dedicated Option Chain architecture

**Recommended:** revise PDS-01 so D-05 is Tier-3/dedicated rather than duplicate
the dense table inside Dashboard.

Then add target-strike navigation over the existing `oc-live-sync` channel.

Files:
- `docs/01_Product_Architecture/PDS-01_Dashboard.md`
- `frontend/Dashboard/chain/chain-sync.js`
- `frontend/OptionChain/option-chain.js`
- D-01/D-07/D-11/D-12 action sources

### A2. Persistent feed state
Files:
- `ws-manager.js`
- `data-service.js`
- `AppState` or new feed-state store
- `chain-template.js`

### A3. 1280 Compact breakpoint
Files:
- `responsive.css`
- `layout.css`
- remove inline grid styles from renderer/templates

### A4. Clean D-04
Move:
- OI Flow → D-07
- Capital Flow → D-07/D-11
Add:
- Max Pain canonical display
- Vol/OI summary

### A5. Implement `focusDashboardStrike(strike)`
Wire D-01/D-07/D-11/D-12.

---

## Phase B — Metric ownership

1. D-04 owns Max Pain.
2. D-11 owns capital walls.
3. D-01 consumes references only.
4. Decide whether Range GEX/Gamma Flip aggregation remains approved frontend derivation or becomes backend canonical.
5. Remove card-specific duplicated calculations where canonical outputs already exist.

---

## Phase C — Navigation/accessibility

1. Zone-only jump navigation.
2. Separate utility rail.
3. One ModalManager focus/backdrop contract.
4. Convert chart-click `<div>` wrappers to buttons.
5. Restore D-00 fund pill.

---

## Phase D — Rendering/performance

1. Add changed-key/signature invalidation.
2. Stop unconditional Greeks chart redraw.
3. Convert highest-frequency whole-card `outerHTML` patches to field patches.
4. Defer hidden Tier-3 heavy rendering.
5. Keep structural full-card rebuild only for actual structural change.

---

# 10. Exact First Coding Pass

The **first coding pass should contain only these five objectives**:

```text
1. Revise/confirm D-05 as the existing dedicated Option Chain surface.
2. Add `openOptionChainAtStrike()` / `oc-focus-strike` drill-down.
3. Persistent LIVE/STALE/DISCONNECTED in D-00.
4. Compact breakpoint at 1280 -> single-column grids.
5. D-04 stripped to positioning metrics only.
```

Do not redesign D-09–D-17 in the same pass. They are already close enough and changing them simultaneously increases regression risk.

---

# 11. Proposed Post-Pass Dashboard Order

```text
D-00 STATUS
    ↓
D-01 DECISION ENGINE
    ↓
STRUCTURE & POSITIONING
    D-02 Market Health & Story
    D-03 Greeks / Net GEX
    D-04 Option Chain Snapshot
    D-05 Option Chain
    D-06 Greeks by Moneyness
    ↓
CAPITAL FLOW
    D-07 Vol/OI Velocity + OI Flow
    D-08 FII/DII Summary
    ↓
INSTITUTIONAL
    D-09 Market Regime & Smart Money
    D-10 Institutional Footprint
    D-11 Capital Concentration
    D-12 Institutional Activity Crux
    ↓
CONFIRMATION (collapsed)
    D-13 Volatility
    D-14 Probability
    D-15 Scenario Analysis
    D-16 Advanced Analytics
    D-17 Strategy Simulator (conditional)
```

---

# 12. Final Recommendation

Do **not** perform another broad visual redesign yet.

The current code has already implemented most of the intended zone architecture.
The highest-value work now is **contract alignment**:

- restore the missing required surface;
- establish trustworthy feed freshness;
- fix responsive behavior;
- enforce metric/card ownership;
- complete drill-down and accessibility.

Once these are done, PDS-01 can become the true implementation source of truth rather than a document that is only approximately reflected in the UI.

**Recommended next action:** implement Phase A only, then rerun this audit before proceeding to Phase B.
