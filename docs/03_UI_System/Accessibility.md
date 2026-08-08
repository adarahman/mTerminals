# Accessibility


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


Minimum contract:
- keyboard operation for every action;
- visible focus;
- semantic buttons/headers;
- labels for icons/controls;
- status not color-only;
- modal focus trap/restore;
- reasonable contrast;
- zoom does not destroy access to controls/data.

Dense financial data is not exempt from accessibility.

## Implementation status

Loaded actions use native buttons or keyboard-equivalent roles, icon controls
have accessible names, focus is visible, feed states include text, and shared
modal behavior traps/restores focus. Browser tests cover modal focus and compact
viewport access; CI rejects inaccessible live popover controls.
