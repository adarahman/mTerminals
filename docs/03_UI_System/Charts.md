# Chart Standard


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


Every chart SHALL have:
- a question;
- units;
- meaningful scale;
- stable semantic color mapping;
- empty/loading/error behavior.

Charts SHOULD:
- redraw only on relevant changes;
- preserve interaction;
- avoid legends when direct labels are clearer;
- expose underlying numeric values through tooltip/table where necessary.

Dashboard charts are explanatory, not decorative.

## Implementation status

Every shipped canvas has an accessible question and unit-bearing label plus
fallback text. Numeric summaries/tables accompany analytical charts, canvas
resizing is change-guarded, and reduced-motion rules apply to chart surfaces.
