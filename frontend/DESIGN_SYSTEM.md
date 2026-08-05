I've consolidated everything into a single, cohesive **mTerminals Product Blueprint**. It removes repetition, resolves overlaps, and organizes the vision into a logical sequence that can become the foundation for your documentation. This consolidation is based on the material you shared. 

---

# mTerminals 2.0 – Product Vision, Design System & Development Blueprint

**Version:** 1.0
**Status:** Foundational Specification
**Purpose:** This document serves as the single source of truth for the design, architecture, user experience, engineering standards, and long-term evolution of mTerminals.

---

# Table of Contents

```text
1. Product Vision
2. Product Principles
3. Design Philosophy
4. Design Language
5. Information Architecture
6. Visual Identity
7. Color System
8. Typography
9. Spacing System
10. Elevation & Layers
11. Border Radius
12. Icons
13. Motion System
14. Layout System
15. Component Standards
16. Trading Design Standards
17. Dashboard Architecture
18. Order Terminal Specification
19. Portfolio Specification
20. Component Library
21. CSS Architecture
22. Application Architecture
23. Development Roadmap
24. Documentation Standards
25. Coding Standards
26. Accessibility
27. Performance Guidelines
28. Definition of Done
29. Long-Term Vision
```

---

# 1. Product Vision

## Mission

> **Build the most intuitive, intelligent, and information-rich options trading terminal for Indian traders.**

The goal is not merely to create a visually attractive application, but to minimize the time between **identifying an opportunity and executing the correct trade**.

mTerminals should evolve from a collection of tools into a unified trading platform where every screen contributes to a trader's workflow.

---

# 2. Product Principles

Every enhancement must satisfy three fundamental questions:

1. **Does it help the trader make a better decision?**
2. **Does it simplify the codebase?**
3. **Will it still make sense a year from now?**

If any answer is **No**, the feature should be reconsidered.

---

# 3. Design Philosophy

Every UI decision follows five principles.

## Information Before Decoration

Information should always have visual priority.

Decorative elements exist only to improve comprehension.

---

## Progressive Disclosure

Present functionality in layers.

```text
Basic

↓

Advanced

↓

Expert
```

Only reveal complexity when required.

---

## One Panel = One Purpose

Each panel should have a clearly defined responsibility.

Examples:

* Order Panel → Execute trades
* Portfolio → Monitor positions
* Decision Engine → Recommend actions
* Analytics → Explain market behaviour

---

## Consistency Over Creativity

Buttons, dialogs, cards, tables, and interactions should behave consistently across the application.

Consistency builds user confidence.

---

## Speed First

The interface should always feel responsive.

Priorities:

* Minimal latency
* Efficient rendering
* Lightweight interactions
* Immediate feedback

---

# 4. Design Language

The application should communicate:

```text
Professional
Elegant
Focused
Modern
Calm
High Information Density
Premium
```

Avoid:

```text
Gaming
RGB Effects
Neon
Flashy Animations
Cartoon Styling
```

---

# 5. Information Architecture

The application should guide the trader naturally.

```text
Market Overview
        ↓
Market Analysis
        ↓
Decision Engine
        ↓
Order Execution
        ↓
Portfolio Management
        ↓
Performance Review
```

Every module should contribute to this end-to-end workflow.

---

# 6. Visual Identity

The design language should reflect professional trading software inspired by:

* Bloomberg
* TradingView
* Interactive Brokers
* Thinkorswim
* Linear
* Arc Browser

The emphasis is on clarity, density, consistency, and trust.

---

# 7. Color System

Colors communicate meaning rather than decoration.

| Color          | Meaning           |
| -------------- | ----------------- |
| Blue           | Information       |
| Green          | Buy / Profit      |
| Red            | Sell / Loss       |
| Orange / Amber | Warning           |
| Purple         | Basket / Strategy |
| Cyan           | Analytics         |
| Grey           | Disabled          |

Trading semantics remain consistent throughout the application.

| Item    | Color  |
| ------- | ------ |
| BUY     | Green  |
| SELL    | Red    |
| LONG    | Green  |
| SHORT   | Red    |
| CALL    | Blue   |
| PUT     | Orange |
| LIVE    | Red    |
| PAPER   | Blue   |
| PROFIT  | Green  |
| LOSS    | Red    |
| WARNING | Amber  |

---

# 8. Typography

Use a restrained typography scale.

| Usage          | Size  | Token       |
| -------------- | ----- | ----------- |
| Micro labels   | 10px  | `--fs-2xs`  |
| Tables         | 11px  | `--fs-xs`   |
| Controls       | 12px  | `--fs-sm`   |
| Body           | 14px  | `--fs-base` |
| Section Titles | 18px  | `--fs-lg`   |
| Hero (small)   | 19px  | `--fs-xl`   |
| Hero (medium)  | 22px  | `--fs-2xl`  |
| Hero (large)   | 28px  | `--fs-3xl`  |
| Hero (largest) | 34px  | `--fs-4xl`  |

The first five sizes cover ordinary UI text. The four "hero" sizes are for headline numbers only — verdict calls, exec scores, confidence grades — not for general content.

Avoid unnecessary font-size variations.

---

# 9. Spacing System

Follow a consistent spacing scale.

```text
4
8
12
16
24
32
48
64
```

No arbitrary spacing values.

---

# 10. Elevation & Layers

```text
Level 0
Application Background

↓

Level 1
Dashboard Cards

↓

Level 2
Floating Panels

↓

Level 3
Dialogs & Modals
```

Users should immediately understand interaction depth.

---

# 11. Border Radius

Standardize corner treatments.

```text
4px
8px
12px
16px
999px (Pill)
```

---

# 12. Icons

Use a single icon library.

**Recommended:** Lucide Icons

Benefits:

* Lightweight
* Modern
* SVG-based
* Consistent
* Tree-shakeable

---

# 13. Motion System

Animations should support interaction rather than attract attention.

Guidelines:

* Duration: 100–250 ms
* Smooth easing
* No bounce effects
* No distracting transitions

---

# 14. Layout System

Every screen follows a predictable hierarchy.

```text
Header

↓

Toolbar

↓

Primary Content

↓

Secondary Content

↓

Footer / Actions
```

This consistency improves navigation and learnability.

---

# 15. Component Standards

Every reusable component defines:

* Default
* Hover
* Active
* Focus
* Disabled
* Loading
* Error

Applies to:

* Buttons
* Inputs
* Dropdowns
* Cards
* Tables
* Chips
* Dialogs
* Tooltips
* Toasts

---

# 16. Trading Design Standards

## Tables

Support:

* Sticky headers
* Sorting
* Keyboard navigation
* Compact density
* Optional zebra striping
* Status chips instead of colored text

## Keyboard Shortcuts

Examples:

| Shortcut     | Action              |
| ------------ | ------------------- |
| Ctrl/Cmd + K | Global Search       |
| F            | Focus Symbol Search |
| Enter        | Confirm Order       |
| Esc          | Close Dialog        |
| Ctrl/Cmd + B | Basket              |
| Ctrl/Cmd + O | Order Ticket        |

---

# 17. Dashboard Architecture

The dashboard represents a complete trading journey.

```text
Market Overview
│ Spot │ VIX │ PCR │ Breadth │ Market Status │

↓

Option Chain
│ OI │ Volume │ Greeks │ IV │ Premium │

↓

Decision Engine
│ AI │ OI │ Greeks │ Risk │ Confidence │

↓

Order Terminal
│ Instrument │ Basket │ Summary │

↓

Portfolio
│ Positions │ Orders │ Margin │ Analytics │

↓

Performance Review
│ Journal │ Statistics │ Heatmap │
```

---

# 18. Order Terminal Specification

Replace the current form-based interface with a guided workflow.

```text
Instrument
↓

Order Configuration
↓

Live Summary
↓

Basket
↓

Review
↓

Place Order
```

Features include:

* Smart Symbol Search
* Grouped Expiry Selection
* Quantity Stepper
* BUY / SELL Toggle
* Order Types
* Live Premium
* Margin Estimation
* Charges
* Basket Management
* Confirmation Dialog

---

# 19. Portfolio Specification

Transform reports into a trading dashboard.

### KPI Section

* Net P&L
* Today's P&L
* Margin Used
* Available Margin
* Open Positions
* Win Rate

### Main Workspace

* Positions
* Orders
* Trade Log
* Analytics

Trade Log enhancements include filtering, search, expandable rows, export, and detailed execution information.

---

# 20. Component Library

**Status (2026-08-05): aspirational, not implemented.** No `MT`-prefixed
component classes exist anywhere in the codebase. What actually exists is
feature-scoped, one-off CSS — each module defines its own button/card/
badge/table/modal rather than sharing a base component. This section
documents the current reality so the doc stops promising something the
CSS doesn't deliver; unifying these into a real shared library (the
original `MTButton`/`MTCard`/etc. vision below) remains a valid future
goal, just not yet started.

| Concept | Actual classes in use | Notes |
| --- | --- | --- |
| Button | `.sec-btn`, `.pt-submit`, `.bt-field-run button`, `.pt-toggle-btn` | Each styled independently, no shared base |
| Card | `.section-card`, `.exec-card`, `.bt-stat`, `.algn-card` | Different padding/radius/border per feature |
| Modal / panel | `.u-modal-overlay` (theme.css) is the closest shared base | `#algo-panel`, `#pt-order-panel`, `#pt-portfolio-panel`, `.bt-panel` each redefine their own chrome instead of extending it |
| Badge / chip | **`.uc-badge` (theme.css) + `.algo-badge`/`.pt-side-badge`/`.fd-sector-tag`, unified via `components.css` (2026-08-05)** | Structure (shape/size) now shared in one place; color variants stay per-feature. `.dec-badge` intentionally excluded — different size tier, see components.css |
| Toast | `.pt-toast`, `.recon-toast` | Two separate toast systems, different positioning/z-index |
| Table | `.t`, `.pt-table`, `.dd-tbl`, `.bt-table` | Four separate table implementations |
| Stat card | `.bt-stat`, `.dd-tbl-metric` | Similar idea, not unified |
| Input / dropdown / tooltip / order card / position row | — | No equivalent found anywhere |

## Original vision (not yet built)

```text
MTButton
MTCard
MTInput
MTDropdown
MTChip
MTBadge
MTTable
MTModal
MTToast
MTTooltip
MTStatCard
MTOrderCard
MTPositionRow
```

Each component should define appearance, behaviour, states, spacing, accessibility, and usage — see Section 15's default/hover/active/focus/disabled/loading/error states, which today are each implemented ad hoc per feature rather than enforced by a shared base.

---

# 21. CSS Architecture

Separate responsibilities clearly.

```text
styles/

tokens.css
theme.css
components.css
buttons.css
cards.css
tables.css
forms.css
chips.css
dialogs.css
animations.css
utilities.css

features/
    dashboard.css
    order-terminal.css
    portfolio.css
```

Responsibilities:

* **tokens.css** → Design tokens
* **theme.css** → Global styles
* **components.css** → Shared components
* **Feature CSS** → Module-specific styling

---

# 22. Application Architecture

Maintain strict separation of concerns.

```text
Design System
        │
        ▼
UI Components
        │
        ▼
Application Layer
        │
        ▼
Business Logic
        │
        ▼
Backend
```

Principles:

* Business logic never references HTML or CSS.
* UI never performs trading calculations.
* Components communicate through clear interfaces.

---

# 23. Development Roadmap

## Sprint 1

Foundation

* Design System
* Component Library

## Sprint 2

Premium Order Terminal

## Sprint 3

Portfolio Dashboard

## Sprint 4

Trade Log & Basket

## Sprint 5

Strategy Workspace

## Sprint 6

Dashboard Polish

Every sprint includes:

1. UX Design
2. Specification
3. HTML
4. CSS
5. JavaScript Integration
6. Testing
7. Accessibility Review
8. Documentation

---

# 24. Documentation Standards

Every major feature must include:

```text
Specification
↓

Wireframe

↓

Implementation

↓

Testing Checklist

↓

Release Notes
```

Recommended documentation structure:

```text
docs/

PRODUCT_VISION.md
DESIGN_SYSTEM.md
COMPONENT_LIBRARY.md
DASHBOARD_GUIDELINES.md
ORDER_TERMINAL_SPEC.md
PORTFOLIO_SPEC.md
CODING_STANDARDS.md
ROADMAP.md
```

These documents become the project's authoritative reference.

---

# 25. Coding Standards

All new code should be:

* Modular
* Reusable
* Theme-aware
* Responsive
* Accessible
* Keyboard-friendly
* High-performance
* Well-documented
* Easy to test
* Easy to extend

Avoid duplication and keep responsibilities clearly separated.

---

# 26. Accessibility

Requirements:

* Visible focus indicators
* Adequate color contrast
* Icons paired with labels where appropriate
* Avoid relying solely on color for meaning
* Minimum touch target size of 40 × 40 px
* Full keyboard navigation support

---

# 27. Performance Guidelines

Design decisions should support performance.

* Minimize DOM depth
* Prefer CSS transforms for animations
* Limit heavy shadows and blur effects
* Lazy-load expensive components
* Avoid unnecessary re-renders
* Keep interactions responsive

---

# 28. Definition of Done

A feature is complete only when:

* Functionality works correctly
* UI follows the Design System
* Responsive behaviour is verified
* Keyboard navigation is functional
* Accessibility review is complete
* Documentation is updated
* Code passes review and testing

---

# 29. Long-Term Vision

mTerminals should evolve into a unified, professional trading platform where analytics, market data, AI insights, decision support, execution, portfolio management, and performance review operate as one cohesive workflow. The long-term strategy is to preserve the existing business logic while modernizing the user experience through a robust design system, reusable component library, modular architecture, and disciplined incremental development. This document serves as the project's constitutional blueprint, ensuring every future design and engineering decision remains consistent, maintainable, and aligned with the product vision.
