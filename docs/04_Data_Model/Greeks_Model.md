# Greeks Model


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Primitives

Delta, Gamma, Theta, Vega, IV plus timestamp/source.

## Source rule

Where broker APIs supply pre-computed Greeks, parsing them does not imply that the
client/backend recomputed them.

## Derived exposure

Delta/Gamma exposure requires verified Greek inputs and documented units.

## Live vs scenario

Live Greeks and scenario-adjusted Greeks are separate namespaces/labels.
