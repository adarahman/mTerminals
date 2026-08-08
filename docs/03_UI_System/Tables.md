# Table Standard


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Dense trading tables

- Stable column order.
- Right-align numeric values.
- Central strike anchor where applicable.
- Sticky header permitted.
- ATM/current row has semantic label + visual cue.
- Local horizontal scrolling.
- Row updates preserve scroll/selection.

## Formatting

Use shared formatters for K/L/Cr, percentages, prices and Greeks.
Do not embed formatting formulas independently in every table.

## Implementation status

Option Chain and portfolio tables retain stable column/strike anchors, sticky
headers, local overflow, accessible ATM/selection labels and preserved row/scroll
state. Shared formatters and tabular numeric styles provide consistent values.
