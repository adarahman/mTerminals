# Legacy quarantine

The target architecture does not treat root-level mixed modules as reusable
application layers. They remain temporary runtime compatibility code while
their behavior is extracted behind package boundaries.

## Rules

- `core`, `decision`, `strategy`, `risk`, `execution`, `analytics`, `oi`,
  `storage`, and `application` must not import legacy mixed modules.
- New `market` domain packages must not import them.
- A legacy dependency is permitted only at an explicitly listed outer adapter
  or composition seam, enforced by `test_architecture_boundaries.py`.
- The allowlist may shrink during migration. New entries require an explicit
  architectural decision and must not be domain or application modules.
- Root `config.py`, `paths.py`, and `logging_config.py` are compatibility
  re-export shims. Canonical implementations live under `infrastructure/`.

## Current temporary seams

None. The enforced allowlist is empty.

Root `market_api.py`, `expiry_manager.py`, and `tick_pipeline.py` are module
aliases for their canonical implementations under `market/`; they contain no
business implementation. Other mixed root modules may still compose the
running legacy entry path, but no new-architecture package imports them.
