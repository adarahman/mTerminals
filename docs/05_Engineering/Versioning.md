# Versioning and Change Control


> **Product:** mTerminals
> **Architecture baseline:** 2026-08-08 implementation
> **Status:** Implemented; requirements CI-enforced where automatable
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
- `v1.4.0` — operational readiness: deployment preflight, repeatable smoke
  checks and backup/restore incident procedures.
- `v1.4.1` — bounded live OI-velocity history and stable pipeline cadence.
- `v1.5.0` — published complete architecture package and release baseline.

The changelog may contain later unreleased conformance work. Unreleased entries
do not retroactively change a published tag's contents.

Annotated release tags SHALL point to a commit that passed the repository's
three CI jobs and the applicable manual smoke checklist. A tag SHALL not be
moved after publication; corrections use a new patch version.

## Architecture Decision Records

For irreversible/high-impact choices, add an ADR rather than burying rationale in code comments.
