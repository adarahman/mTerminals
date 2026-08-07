# Versioning and Change Control


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Product documents

Major version:
- zone/surface mission changes;
- ownership model changes;
- incompatible workflow changes.

Minor version:
- new card/metric contract;
- new state/interaction rule;
- clarified architecture with implementation impact.

Patch/document revision:
- wording/correction with no behavioral change.

## Code releases

Code versioning MAY remain independent, but releases SHOULD record which PDS
versions they implement.

## Architecture Decision Records

For irreversible/high-impact choices, add an ADR rather than burying rationale in code comments.
