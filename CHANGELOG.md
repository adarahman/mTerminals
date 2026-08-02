## [Unreleased]

### Added
- Account-level risk guard for live trading (`risk/account_guard.py`):
  a daily max-loss cutoff, a max open-exposure cap checked against the
  broker's own live position book, and a consecutive-drawdown circuit
  breaker — all evaluated across the whole trading day, not just per
  order, and persisted across a server restart. Trips the same
  kill-switch file `ws_server_live.py` already checks. See
  `PROJECT-ARCHITECTURE.md` §11.
- Strategy → execution bridge (`decision/auto_executor.py`): turns
  DecisionEngine's per-tick bias/action into automated live orders,
  gated behind its own master switch (`AUTO_STRATEGY_EXECUTION_ENABLED`,
  independent of `LIVE_TRADING_ENABLED`) plus confidence/conflict/
  cooldown/daily-cap checks, and routed through the exact same manual
  order path (lot-size verification, rate limit, account_guard) so an
  auto-executed order is checked identically to a human-submitted one.
  Single-leg action types only in v1. See `PROJECT-ARCHITECTURE.md` §12.

### Fixed
- `ml/inference.py` was missing `import pandas as pd`, so every
  VirtualOI inference call silently `NameError`'d and fell back to a
  stale `0.0` prediction instead of actually running the model — no
  error surfaced because the surrounding `try/except` swallowed it.

## [2.1.0] - 2026-07-26

### Added
- New architecture reference document covering the frontend/backend
  module layout.
- Standalone strike detail report view.
- Shared Smart Money / Market Structure signal utilities, now used
  consistently across the dashboard and the standalone option chain.

### Changed
- Reorganized the option chain's frontend module files into a flatter,
  simpler folder structure.
- Reworked panel layout across the dashboard, including the decision
  details, IV surface grid, and opening view panels.
- Refined Smart Money / Market Structure signal logic and the option
  chain's composite signal analysis.

### Fixed
- Orders placed from the standalone Option Chain tab (via the CE/PE
  LTP quick-order popup) no longer hang on "Sending…" and time out
  with "No response — is the dashboard tab open?" even when the
  dashboard tab is open. The handler that was supposed to receive and
  forward these orders to the paper trading engine was missing, so
  the request silently failed instead of reaching the order book.
  Orders from the standalone tab now go through the same order path
  as every other order surface.

### Removed
- Two unused backend data-fetching scripts that were no longer part
  of any active workflow.

## [2.0.1] - 2026-07-23

### Fixed
- Option chain ATM range selector (±3 / ±5 / ±10 / All) no longer resets
  to the default 10-strike view moments after being manually selected.
  Live sync from the dashboard was overwriting the user's chosen range
  on every incoming snapshot; the user's selection is now preserved
  once set.