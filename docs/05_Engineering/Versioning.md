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

Current release line:

- `v1.0.0` — PDS-00 through PDS-08 implementation baseline;
- `v1.1.0` — production hardening: health visibility, feed-state reasons,
  deterministic reconnect/recovery tests and rollback readiness.
- `v1.2.0` — observability: structured redacted logs and payload-free
  operational metrics;
- `v1.3.0` — supply-chain hardening: immutable CI action pins, vulnerability
  gates and bounded automated dependency updates.

Annotated release tags SHALL point to a commit that passed the repository's
three CI jobs and the applicable manual smoke checklist. A tag SHALL not be
moved after publication; corrections use a new patch version.

## Architecture Decision Records

For irreversible/high-impact choices, add an ADR rather than burying rationale in code comments.
