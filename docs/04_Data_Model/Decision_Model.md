# Decision Model


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


A decision object SHOULD expose:
- bias/action;
- confidence;
- trade grade;
- evidence contributors;
- warnings;
- important levels;
- strategy recommendation;
- timestamp/version;
- degraded-input flags.

Confidence is not a profit guarantee.

Decision state is derived from canonical analytics and is independent of UI visibility.
