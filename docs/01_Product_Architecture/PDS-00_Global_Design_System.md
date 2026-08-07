# PDS-00 — Global Design System


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## 1. Mission

mTerminals SHALL feel like a professional trading instrument: calm, dense,
fast and predictable. Visual design exists to reduce time from observation to
decision, not to decorate the screen.

## 2. Product design principles

1. **Decision before data.** Primary conclusions appear before evidence.
2. **One panel, one purpose.** Every card answers one trading question.
3. **One canonical metric owner.** Repetition may occur; recomputation may not.
4. **Progressive disclosure.** Tier 1 = decision, Tier 2 = explanation,
   Tier 3 = exploration.
5. **Consistency over novelty.** A known interaction pattern beats a clever one.
6. **Calm live data.** Changes patch in place without layout churn.
7. **Trading semantics are stable.** Bullish/bearish/warning/neutral meanings
   do not change from page to page.
8. **Dense, not cramped.** Information density is encouraged; visual noise is not.

## 3. Information tiers

| Tier | Purpose | Default treatment |
|---|---|---|
| 1 | Immediate action/context | Always visible |
| 2 | Explanation/validation | Visible or compact |
| 3 | Deep investigation | Modal, detail view, collapsible |

A Tier-3 feature SHALL NOT enlarge a Tier-1 card's default footprint.

## 4. Typography

The implementation SHALL expose semantic typography roles instead of ad-hoc sizes:

- Display Numeric — spot, P&L, headline confidence.
- Page Title.
- Zone Title.
- Card Title.
- Metric Label.
- Metric Value.
- Body.
- Caption/Metadata.
- Table Numeric.
- Monospace/Diagnostic only where technically meaningful.

Tabular/numeric displays SHOULD use tabular figures to minimize jitter.

## 5. Semantic colors

Color communicates meaning, not ownership.

| Semantic role | Meaning |
|---|---|
| Positive / Bullish | Constructive direction, profit, buy-side state |
| Negative / Bearish | Adverse direction, loss, sell-side state |
| Warning | Risk, degraded data, trap/uncertainty |
| Information | Context, neutral analytics |
| Muted | Secondary/disabled/supporting content |

No card gets a unique accent merely to make it look different.

## 6. Layout system

- Desktop uses stable multi-column grids.
- Cards align to a shared spacing rhythm.
- Zone boundaries are visually clear.
- Visual weight decreases as decision priority decreases.
- Full-width analytical tables/charts SHALL not force unrelated cards to match
  their content height.
- The Decision Engine is always the dominant dashboard element.

## 7. Card anatomy

```text
┌ Header ─────────────────────────────┐
│ Title        metadata / affordance  │
├─────────────────────────────────────┤
│ Tier-1 answer                       │
│ Tier-2 explanation                  │
├ optional footer ────────────────────┤
│ timestamp / source / secondary info │
└─────────────────────────────────────┘
```

Headers that open detail surfaces SHALL be the click target. Separate `View`,
`Open`, `Full Chain`, or `Details` buttons SHOULD be removed unless the action
is genuinely distinct.

## 8. Interaction states

Every interactive element SHALL support:

- default;
- hover;
- keyboard focus;
- active/pressed where applicable;
- disabled where applicable.

Data states SHALL distinguish:

- loading;
- live;
- partial;
- stale;
- disconnected;
- error;
- empty/market-closed where appropriate.

`0` is data. It SHALL never substitute for unavailable/loading.

## 9. Motion

- Ordinary transitions SHOULD be subtle and short.
- Live numerical changes SHALL not bounce, repeatedly flash or resize layout.
- Motion is for state continuity, not spectacle.
- Reduced-motion preference SHALL be respected.
- Modal and hover transitions SHOULD generally remain within ~150–250 ms.

## 10. Modal standard

Every modal SHALL:

- have a clear title;
- close with a close control;
- close with `Esc`;
- close on safe backdrop click;
- trap focus while open;
- restore focus to its invoker;
- remain open across live ticks;
- not rebuild merely because unrelated data changed.

## 11. Table standard

- Numeric columns align consistently.
- ATM/current context receives a clear but non-destructive highlight.
- Sticky headers MAY be used for dense tables.
- Sorting/filtering SHALL not change metric semantics.
- Horizontal overflow SHALL be contained locally.
- Table redraw SHALL preserve user scroll and row expansion state.

## 12. Chart standard

Charts SHALL answer a specific question. A chart with no decision/explanation
purpose does not belong on an executive surface.

- Avoid unnecessary legends.
- Labels and units are mandatory.
- Redraw only when relevant inputs change.
- Crosshair/tooltips SHOULD not block live updates.
- Trading semantics remain consistent with tables/cards.

## 13. Accessibility

- All actions keyboard operable.
- Focus visible.
- Color is never the only carrier of meaning.
- Controls have accessible names.
- Clickable headers use interactive semantics.
- Modal focus behavior is deterministic.

## 14. Responsive system

- Desktop: >=1280px.
- Compact: <1280px.
- Compact preserves source order and collapses multi-column grids to one column
  unless a surface-specific PDS states otherwise.
- Phone-specific design is deferred unless separately specified.

## 15. Definition of done

A UI component is not complete until it has:

- defined question/purpose;
- canonical data source;
- loading/live/stale/error behavior;
- keyboard interaction;
- responsive behavior;
- tick-update behavior;
- acceptance criteria or parent-PDS coverage.
