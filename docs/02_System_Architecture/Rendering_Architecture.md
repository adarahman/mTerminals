# Rendering Architecture


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Objective

Live data should update the minimum necessary visual surface while preserving
user interaction state.

## Pipeline

```text
WebSocket message
      ↓
WSManager validation
      ↓
MarketStore ingest/full/delta
      ↓
State version / changed keys
      ↓
Derived/view models
      ↓
Panel/component invalidation
      ↓
Targeted DOM or canvas patch
```

## Current state

The project already uses `setHtmlIfChanged`, `sizeCanvasIfChanged` and a shared
`patchOuterHtmlIfChanged` approach in the chain renderer. This reduces redundant
writes but still builds full HTML strings for several cards on each tick.

## Target

Move hot paths toward field-level or row-level patching where profiling justifies it.

## Render priorities

1. Status/feed freshness.
2. Decision Engine.
3. Visible executive cards.
4. Visible dense tables/charts.
5. Hidden/collapsed expensive visualizations.

Data freshness and render priority are separate: hidden cards still receive state.

## User-state preservation

Render operations SHALL preserve:
- modal state;
- details open/closed state;
- D-05 scroll;
- selected/expanded strike;
- slider/drag state;
- chart pan/zoom when possible.

## Canvas

A chart SHALL redraw only if:
- relevant data changed;
- dimensions changed;
- user interaction requires it.

## Forbidden

- full-page rerender per tick;
- rebuilding a modal simply because market state changed;
- resetting scroll to ATM every tick;
- reading rendered text back as analytical input.
