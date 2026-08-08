# Card Standard


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Anatomy

Header → Tier-1 answer → Tier-2 explanation → optional metadata.

## Header navigation

If the card has one obvious Tier-3 destination, the full header is the affordance.
Use a consistent navigation indicator/hover treatment.

## Prohibited patterns

- separate `View` button duplicating header action;
- multiple unrelated actions in card header;
- duplicate metric calculations;
- excessive badges competing with the primary answer.

## Live behavior

Text/numbers patch in place. Card root replacement SHOULD be minimized in hot paths.

## Implementation status

Navigable cards use consistent header affordances. Hot top-bar and ticker values
patch in place; remaining card-root updates are diff-guarded and preserve active
details and click state.
