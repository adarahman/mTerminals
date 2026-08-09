# mTerminals — Project Architecture Map

One reference doc covering both halves of the project: the frontend
(`DashboardPro.html` + its JS split files) and the Python backend
(`Backend.zip` + `ws_server_live.py`). Includes which file owns which
dashboard section, the live script/import load order, and a redundancy
review flagging files that are safe to delete.

**Contents**
1. [Cleanup summary — safe to delete](#1-cleanup-summary--safe-to-delete)
2. [Frontend — script load order](#2-frontend--script-load-order)
3. [Frontend — what `dashboard.js` actually is](#3-frontend--what-dashboardjs-actually-is)
4. [Frontend — section-wise file map](#4-frontend--section-wise-file-map)
5. [Frontend — cross-cutting infrastructure](#5-frontend--cross-cutting-infrastructure)
6. [Frontend — files referenced but not in this snapshot](#6-frontend--files-referenced-but-not-in-this-snapshot)
7. [Backend — entry point](#7-backend--entry-point)
8. [Backend — live import graph](#8-backend--live-import-graph)
9. [Backend — file-by-file status](#9-backend--file-by-file-status)
10. [Housekeeping notes](#10-housekeeping-notes)

---

## 1. Cleanup summary

*Updated after a code-cleanup pass (dead code + duplication only, no
behavior/perf changes). Status below reflects the codebase as it stands
now.*

`volume_profile.py` was removed after confirming that it had no runtime,
test, CLI, or documented product consumer. Its stale `py-modules` packaging
entry was removed at the same time, so editable and wheel installs remain
internally consistent.
whoever owns the pyproject.toml entry before touching it again.

**Done this pass:**
```
Frontend:
  Dashboard/chain/chain-renderer.js's _rerenderChainPanels() had the same
  6-10 line "build fresh HTML → diff against dataset.lastHtml → outerHTML
  swap → rebind click guard" block hand-rolled 7 times (chain-summary-card,
  oi-flow-summary-card, greeks-alerts-card, fiidii-summary-card,
  inst-activity-summary-card, advanced-analytics-card, exec-section-wrap).
  Consolidated into one shared patchOuterHtmlIfChanged(elId, buildHtml, opts)
  helper in shared/utils/dom-utils.js (next to setHtmlIfChanged, which it
  mirrors for the outerHTML/whole-element case). Call sites are now 1-4
  lines each; behavior preserved exactly, including the per-card click-guard
  keys and advanced-analytics-card's <details open> state preservation.
  File dropped from 1364 → ~1300 lines. This is the module in the direct
  per-tick render path, so it's the first place to look if live updates
  still feel laggy after this — see note below.
```

**Already resolved before this pass (architecture doc was stale on these):**
`decision-card-preview.html`, the `paper_trading.py` independent `LOT_SIZES`
duplicate, and `engine.py`'s deprecated `velocity_window_minutes` parameter
were all previously flagged here but are confirmed gone from the current
codebase — no action needed. `nse_bse_fundamentals.py`, `compare_feeds.py`,
and the `__MACOSX/` zip artifact were already resolved in an earlier pass.

**Checked and NOT duplication (same name, different code — left alone):**
`safe_float()` in `brokers/smartapi_client.py` vs `market_api.py` (trivial
try/float coercion vs NSE/BSE-specific string cleanup — different logic,
merging would be a behavior change for no benefit). `get_lot_size()` in
`lot_sizes.py` vs `brokers/smartapi_instruments.py` (proper layering: the
former wraps the latter with caching/fallback, not a duplicate). Private
`_build_strategies()` in `mTerminals_json.py` vs `strategy/strategies.py`
(unrelated functions, different signatures, coincidental name only).

**Keep, even though unimported:** `build_training_warehouse.py`,
`ml/training.py`, and `diag_lotsize.py` are intentional standalone/manual
tools, not dead code — see §9.

**Not addressed this pass (perf, out of scope):** the underlying reason
`_rerenderChainPanels` exists at all — full outerHTML string rebuild/diff
per card on every WS tick rather than field-level DOM patching — is a
performance question, not a duplication one. The consolidation above makes
that hot path shorter and easier to reason about but doesn't change its
algorithmic cost. If live-update lag persists, that's the next thing to
profile (worth checking how many ticks/second arrive vs. how expensive each
card's `build*Html()` call is).

---

## 2. Frontend — script load order

From `DashboardPro.html`. Order matters — later files call into
classes/functions declared earlier.

```
app-state.js
event-bus.js                    ← Phase 5, loads first so every eventBus guard is true
Chart.js (CDN)
chart-legend.js
PriceChart/*.js                 (chart-data, chart-renderer, indicator-engine,
                                  history-loader, price-chart-engine)
formatters.js
dom-utils.js                    ← $i, err, setHtmlIfChanged, sizeCanvasIfChanged
range-tabs.js
chain-helpers.js                ← shared chain/expiry/index pure functions
chain-view.js                   ← MUST load first of the chain-*.js split files
                                   (declares ChainDenseView / RightPanelView / ChainView)
chain-template.js               ← pure HTML builders (attaches to ChainView.prototype)
chain-renderer.js               ← DOM-writing render/patch methods
OptionChain/chain-depth.js
chain-greeks.js
chain-sync.js                   ← in-dashboard strike/detail navigation
OptionChain/chain-utils.js
chain-view-models.js            ← Phase 3: pure business-logic (row/strike view models)
OptionChain/chain-templates.js  ← Phase 3: pure HTML templates consuming those view models
panels-views.js                 ← OiFlowView, ExecView, StrategyView, SimulatorView, ModalManager
ws-manager.js
market-store.js                 ← MarketStore: owns _wsState, ingest()/applyDelta()/deepMerge()
data-service.js
ui-controls.js
panel-manager.js                ← Phase 4: Panel base class + PanelManager registry
dashboard-panels.js             ← Phase 4: 6 Panel subclasses (wraps the above)
dashboard.js                    ← APP BOOTSTRAP ONLY (see §3)
paper-trading-shared.js + order-entry.js + portfolio-tracker.js
```

---

## 3. Frontend — what `dashboard.js` actually is

**Bootstrap only** — no section HTML is built here. It:
- Constructs `app` (`App` class) and all module instances (`app.chain`, `app.chainDense`, `app.oiFlow`, `app.exec`, `app.modal`, etc.)
- Registers the 6 `Panel` subclasses with `PanelManager`
- Exposes legacy `window.*` shims (e.g. `window.renderDashboard`, `window.patchTopBarAndDecision`) so old inline `onclick="..."` markup keeps working
- Wires global event listeners (`load`, `resize`, `pageshow` bfcache guard)
- Auto-connects the WebSocket on `DOMContentLoaded`

---

## 4. Frontend — section-wise file map

### 🔹 Top Bar (`#sec-topbar`)
| File | Role |
|---|---|
| `chain-template.js` | `ChainView.prototype.renderTopBarHtml()` — symbol picker, spot + flash-up/down, %/pt badge, chart icon, expiry/DTE/"As of" pills. Also `renderFundPillHtml()` (P&L/Fund pills), `renderSymbolOptions()` |
| `chain-helpers.js` | `renderIndexTicker()` (NIFTY/BANKNIFTY/VIX pill strip); `getExpirySelectNode()` / `moveExpirySelectIntoTopBar()` — re-parents the persistent `#expirySelect` node |
| `chain-renderer.js` | `ChainView.prototype.patchTopBarAndDecision()` — swaps `#sec-topbar` outerHTML on every live tick |
| `dashboard-panels.js` / `dashboard.js` | `OptionChainPanel.patch()` wraps it; exposed as `window.patchTopBarAndDecision` |
| `DashboardPro.html` | Hosts the persistent `#expiry-select-holder` / `#expirySelect` node |

### 🔹 Decision Bar / Verdict Box (`#sec-decision`)
| File | Role |
|---|---|
| `chain-template.js` | `ChainView.prototype.renderDecisionBoxHtml()` — Tier-1 `.verdict` card (bias/confidence/trade grade/PCR-VIX-MaxPain-Wall strip) + Tier-3 "Decision Detail" collapsible (trap warning, active signals, S&R levels, strategy name) |
| `decision-card-preview.html` | **Static design mockup only** — not wired into the app, no live data. See §1 — safe to delete. |
| `chain-renderer.js` | `patchTopBarAndDecision()` (per-tick) and `renderDashboard()` (full rebuild) both swap `#sec-decision` |
| `dashboard-panels.js` | `DecisionBoxPanel.refresh()` — standalone `PanelManager` refresh path |

### 🔹 Option Chain Snapshot (`#chain-summary-card`)
| File | Role |
|---|---|
| `chain-template.js` | `ChainView.prototype.buildChainSummaryHtml()` — OI totals, PCR, ΔOI shift, Vol/OI card |
| `chain-renderer.js` | Incremental-refresh path diffs/swaps `#chain-summary-card` via `dataset.lastHtml` cache |
| `panels-views.js` | `toggleFullChainFocus()` — "Full Chain →" button; injects an inline iframe (`OptionChain/option-chain.html`) after the card instead of opening a new tab |
| `dom-utils.js` | `setHtmlIfChanged()` — shared diff-and-write helper used by this and most other cards |

### 🔹 Greeks / Net GEX (alerts card + full modal table)
| File | Role |
|---|---|
| `chain-greeks.js` | `buildGreeksAlertsHtml()` (compact alerts card), `buildAtmGreeksDetailHtml()`, `buildIvHvSkewDetailHtml()`, `renderGreeksGex()` (full per-strike Δ/Γ/Θ/Vega + Net GEX table, modal content) |
| `DashboardPro.html` | `#greeks-dashboard-modal` — modal shell, tabs (Δ/Γ/Θ/Vega), range-tab group |

### 🔹 IV Surface (alerts card + full modal table)
| File | Role |
|---|---|
| `chain-template.js` | `buildIvAlertsHtml()` (compact skew/rank alerts), `buildIvSurfaceHtml()` (full per-strike CE/PE IV bar table + Skew/Max/Min footer) |

### 🔹 Option Chain full dense table (standalone tab)
| File | Role |
|---|---|
| `chain-renderer.js` | `ChainDenseView.prototype.buildRowsHtml()`, `refreshView()`, `updateHeader()`, `renderExpiryOptions()` |
| `chain-view-models.js` | Pure business logic — `buildChainRowViewModel()`, `buildStrikeDetailViewModel()`, `buildOiCombinedBarViewModel()` (institutional OI bar + smart-money badge) |
| `OptionChain/chain-templates.js` | Pure HTML rendering of those view models (not in original upload, referenced by header comments) |
| `chain-sync.js` | `ChainDenseView.prototype._initBroadcast()` / `_broadcastToOptionChainTab()` — BroadcastChannel sync to standalone `option-chain.html` tab |

### 🔹 OI Flow
| File | Role |
|---|---|
| `panels-views.js` | `OiFlowView` class — `buildOiTopMoversStrip()`, `buildOiFlowRows()`, `buildOiFlowSummaryHtml()`, `switchOiFlowTab()` |
| `OiFlowView` | Dashboard-native OI summaries derived from the canonical option-chain state |

### 🔹 Executive Dashboard / FII-DII / Institutional Activity
| File | Role |
|---|---|
| `panels-views.js` | `ExecView` class — `renderExecutiveDashboard()`, `buildDriversDraggersCard()`, `buildFiiDiiCard()`, `buildFiiDiiSummaryCard()`, `buildInstitutionalActivitySummaryCard()`, `renderFiiDiiModal()` |
| `ModalManager` (`panels-views.js`) | `openFiiDiiModal()` / `closeFiiDiiModal()` |

### 🔹 Strategy Payoff
| File | Role |
|---|---|
| `panels-views.js` | `StrategyView` class — `renderStratPayoff()`, canvas curve drawing (`drawCurve`), `_populateStrikeDropdown()` |

### 🔹 Institutional F&O Simulator
| File | Role |
|---|---|
| `panels-views.js` | `SimulatorView` class — `simInit()`, `simUpdate()`, `simRenderGEXChart()`, `simRenderVolGrid()`, `simRenderTable()`, near/far strike banding (`instBandFor`, `INST_THRESHOLDS`), Strike Detail table expand/collapse (`expandStrikeDetail()`/`collapseStrikeDetail()`) |

### 🔹 Modals
| File | Role |
|---|---|
| `panels-views.js` | `ModalManager` class — native Greeks/GEX, FII/DII, IV Surface and simulator modal lifecycle |
| `DashboardPro.html` | Native modal shell markup; the retired standalone OI Flow iframe is no longer shipped |

### 🔹 Paper Trading
| File | Role |
|---|---|
| `paper-trading-shared.js` | Shared paper-trading state, calculations, transport helpers, and `ptComputeFundSummary()` |
| `order-entry.js` / `portfolio-tracker.js` | Order-entry and portfolio panel behavior split from the retired paper-trading monolith |
| `dashboard-panels.js` | `PaperTradingPanel` — guarded no-op hook (`window.ptRefreshPanel`) for future explicit refresh |

### 🔹 Price Chart
| File | Role |
|---|---|
| `price-chart-engine.js` | Coordinates chart data, history, indicators, rendering, zoom and order-panel UI |
| `DashboardPro.html` / `modal-manager.js` | Native Price Chart modal, opened from the Decision Engine mini chart |
| `chart-data.js` / `chart-renderer.js` | Tick/history storage and canvas drawing responsibilities |
| `indicator-engine.js` / `history-loader.js` | SMA/EMA calculations and backend OHLCV hydration |
| `chart-legend.js` | Greeks-by-Moneyness legend chart (Chart.js) — separate from the price chart, rendered inline in the dashboard template |

---

## 5. Frontend — cross-cutting infrastructure

| File | Role |
|---|---|
| `dom-utils.js` | `$i()`, `err()`, `setHtmlIfChanged()` (skip innerHTML write if unchanged), `sizeCanvasIfChanged()` (avoid canvas flicker) |
| `event-bus.js` | `EventBus` — pub/sub (`market:update`, `symbol:change`, `expiry:change`, `chain:update`, `chart:refresh`). Introduced but not yet subscribed-to anywhere (additive only, no call sites migrated yet) |
| `market-store.js` | `MarketStore` — single owner of live state (`_wsState`); `ingest()` handles `full`/`delta`/generic WS message shapes; `applyDelta()` patches keyed arrays (e.g. option chain by strike) in place; `deepMerge()` for generic merges |
| `panel-manager.js` | `Panel` base class (init/refresh/resize/destroy lifecycle) + `PanelManager` registry (`register`, `get`, `initAll`, `refreshAll`, `resizeAll`, `destroyAll`) |
| `dashboard-panels.js` | Dashboard-native panel adapters: `OptionChainPanel`, `PaperTradingPanel`, `DecisionBoxPanel`, `MarketBreadthPanel` |
| `range-tabs.js` | Single source of truth for the ±3/±5/±10/±15/All range tab-group markup, injected into every `[data-range-tabs]` placeholder (sidebar, Greeks modal, IV modal) |
| `chain-helpers.js` | Shared pure functions: `activeAtm`, `applyExpirySelection`, `getFilteredChain`, `findGammaFlipStrike`, `chainCombinedSignal`, `velMiniCell`, `oiFlowLabel`, expiry-select re-parenting, mojibake repair |

---

## 6. Frontend — files referenced but not in this snapshot

Exist elsewhere in the project — not part of the files uploaded/reviewed so far:

- `formatters.js` — `fmt`/`fmtN`/`fmtK`/`fmtI`/`sClr`/`ceOiChgClr`/`sign`/`dirClass`/`cell`
- `ws-manager.js` — `WSManager` (raw socket connect/reconnect)
- `data-service.js` — `DataService` (WS/file/paste loading, auto-refresh, render scheduling)
- `ui-controls.js` — `UiControls` (sticky offsets, timer tabs, range/vel flyout, section-jump nav)
- `app-state.js` — `AppState` (e.g. `AppState.lastGreeks`, `AppState.selectedDepthStrike`)
- `paper-trading-shared.js` — shared paper-trading internals and `ptComputeFundSummary()`
- `order-entry.js` / `portfolio-tracker.js` — order and portfolio surfaces
- `OptionChain/chain-depth.js`, `OptionChain/chain-utils.js`, `OptionChain/chain-templates.js`
- `Dashboard/chain/*` — canonical Option Chain snapshot and strike drill-down
- `DashboardPro.html` + `PriceChart/*.js` — native modal price chart

---

## 7. Backend — entry point

`ws_server_live.py` is the live WebSocket server process. It lives at the
**project root, outside the `backend/` package** — easy to miss if you're
only reviewing `backend/`'s folder structure, but it's the true root of the
whole live dependency graph; `engine.py` is the compute core underneath it,
not the top. Its top-level imports, now package-qualified after the
reorg:

```python
from nse_eod_fetch import fetch_all_eod, is_trading_day
from analytics.fii_dii_sentiment import get_report_for_trading_day
from analytics.nse_fii_dii_flow_fetch import record_today_flow, get_flow_series

import option_chain_json
import mTerminals_json
import market_api              # lightweight ticker-strip quotes

from pipeline_config import RuntimeConfig
from paper_trading import PaperTradingEngine, _instrument_key, LOT_SIZES as PT_LOT_SIZES
from brokers.market_data import market_data
from brokers.smartapi_client import (...)
from brokers.smartapi_ws_client import SmartTickStream, EXCHANGE_TYPE
from smartapi_feed_adapter import TickAggregator
from brokers.smartapi_history import get_index_candles, get_candle_data
```

Method used for the backend review: traced every real
`import`/`from ... import` statement starting here — not just comment
mentions, which several files use heavily to document intent without
creating a real dependency (e.g. `# Replaces: option_chain_json._DF_IDX_CACHE`
style refactor notes, which read as imports to a naive grep but aren't).

---

## 8. Backend — live import graph

*Paths below are relative to `backend/`, except the two root-level files
(`ws_server_live.py`, and the root scripts it directly wires together).*

| Module | Imported by |
|---|---|
| `analytics/fii_dii_sentiment.py` | `mTerminals_json.py`, `ml/build_training_warehouse.py`, `ws_server_live.py` |
| `analytics/nse_fii_dii_flow_fetch.py` | `ws_server_live.py` |
| `brokers/market_data.py` | `mTerminals_json.py`, `smartapi_pipeline_adapter.py`, `ws_server_live.py` |
| `brokers/smartapi_client.py` | `brokers/market_data.py`, `brokers/smartapi_ws_client.py`, `smartapi_feed_adapter.py`, `smartapi_pipeline_adapter.py`, `ws_server_live.py` |
| `brokers/smartapi_history.py` | `ws_server_live.py` |
| `brokers/smartapi_instruments.py` | `diag_lotsize.py`, `lot_sizes.py`, `paper_trading.py`, `smartapi_pipeline_adapter.py`, `ws_server_live.py` |
| `brokers/smartapi_ws_client.py` | `smartapi_feed_adapter.py`, `ws_server_live.py` |
| `decision/decision_engine.py` | `mTerminals_json.py` |
| `decision/signal_builder.py` | `decision/decision_engine.py`, `engine.py` |
| `engine.py` | `option_chain_json.py` |
| `expiry_manager.py` | `mTerminals_json.py`, `option_chain_json.py` |
| `index_contributors.py` | `option_chain_json.py` |
| `lot_sizes.py` | `option_chain_json.py`, `smartapi_pipeline_adapter.py` |
| `mTerminals_json.py` | `oi/chain_metrics.py`, `option_chain_json.py`, `strategy/strategies.py`, `ws_server_live.py` |
| `market_api.py` | `option_chain_json.py`, `smartapi_pipeline_adapter.py`, `ws_server_live.py` |
| `ml/inference.py` | `mTerminals_json.py` |
| `nse_eod_fetch.py` | `analytics/fii_dii_sentiment.py`, `backfill_eod.py`, `ws_server_live.py` |
| `oi/oi_analysis.py` | `engine.py`, `ml/build_training_warehouse.py`, `option_chain_json.py` |
| `oi/pricing.py` | `decision/signal_builder.py`, `engine.py`, `mTerminals_json.py`, `oi/chain_metrics.py`, `smartapi_pipeline_adapter.py`, `strategy/strategies.py` |
| `option_chain_json.py` | `ws_server_live.py` |
| `paper_trading.py` | `ws_server_live.py` |
| `pipeline_config.py` | `option_chain_json.py`, `ws_server_live.py` |
| `risk/risk_meters.py` | `engine.py` |
| `smartapi_feed_adapter.py` | `ws_server_live.py` |
| `smartapi_pipeline_adapter.py` | `option_chain_json.py`, `ws_server_live.py` |
| `storage/caches.py` | `analytics/fii_dii_sentiment.py`, `brokers/smartapi_client.py`, `decision/signal_builder.py`, `mTerminals_json.py`, `ml/build_training_warehouse.py`, `oi/oi_analysis.py`, `option_chain_json.py`, `smartapi_pipeline_adapter.py` |
| `strategy/strategies.py` | `engine.py` |

**`mTerminals_json.py` vs `option_chain_json.py`** — still not duplicates,
same as before the reorg: `mTerminals_json.py` is the JSON
serialization/export layer (`fmt_k`, `_safe_num`, payload assembly for the
WS clients, plus its own `DecisionEngine`/`expiry_manager` instantiation
for that payload); `option_chain_json.py` is the fetch/compute orchestrator
(pulls in `engine.py`, `oi/oi_analysis.py`, `smartapi_pipeline_adapter.py`).
Both are imported directly by `ws_server_live.py` and both are actively
used — no circular import exists between them; a couple of comment
mentions of each other's names (not real imports) are just refactor-history
notes.

**Since the last pass:** `expiry_manager.py`, `lot_sizes.py`,
`index_contributors.py`, and `pipeline_config.py` were extracted out of
`option_chain_json.py` as part of an in-progress "v4 migration plan"
(documented in each file's own docstring) — the LOT_SIZES-via-local-import
workaround that used to exist between `smartapi_pipeline_adapter.py` and
`option_chain_json.py` is gone now that both pull from the standalone
`lot_sizes.py` instead.

---

## 9. Backend — file-by-file status

### ✅ Confirmed active — reachable from `ws_server_live.py`'s import graph
```
engine.py                       mTerminals_json.py               option_chain_json.py
market_api.py                   oi/oi_analysis.py                expiry_manager.py
decision/decision_engine.py     ml/inference.py                  paper_trading.py
brokers/smartapi_client.py      brokers/smartapi_ws_client.py    smartapi_feed_adapter.py
brokers/smartapi_history.py     brokers/smartapi_instruments.py  smartapi_pipeline_adapter.py
analytics/fii_dii_sentiment.py  nse_eod_fetch.py                 analytics/nse_fii_dii_flow_fetch.py
lot_sizes.py                    index_contributors.py            pipeline_config.py
decision/signal_builder.py      oi/pricing.py                    risk/risk_meters.py
strategy/strategies.py          brokers/market_data.py           storage/caches.py
oi/chain_metrics.py
```

### 🟡 Not imported by anything — but intentionally standalone (keep)
| File | Why it's not dead |
|---|---|
| `ml/build_training_warehouse.py` | Offline ETL — reads `oi_history_log.parquet`, writes the training warehouse `ml/inference.py`/`ml/training.py` consume *by file path*, not by import. Meant to run manually/on a schedule. This is the renamed successor to the old `virtual_oi_estimator.py` — split into `ml/training.py` (fits the Huber regressor pipelines) and `ml/inference.py` (serves predictions at runtime). |
| `ml/training.py` | Manual/scheduled training script — produces `production_oi_pipeline_ce.pkl` / `_pe.pkl`, which `ml/inference.py` loads by file path, not by import. |
| `tools/diag_lotsize.py` | Manual CLI diagnostic for lot-size resolution. Run from `backend/` with `python -m tools.diag_lotsize`; intentionally not wired into the live server. |
| `backfill_eod.py` | One-off manual backfill for `nse_eod_fetch.py`'s EOD datasets — run by hand, not imported by the live pipeline. |
| `tests/*.py` | Run via `pytest`, not imported by application code — expected to show zero importers. |

### 🗑️ Genuinely orphaned — zero importers anywhere, no standalone-tool justification
None currently. `volume_profile.py`, `nse_bse_fundamentals.py`, and
`compare_feeds.py` have been removed from the codebase. See §1.

---

## 10. Housekeeping notes

- **`decision-card-preview.html`** (frontend) — confirmed unreferenced anywhere in the codebase (not `<script>`'d, not `<link>`'d, no cross-file calls). Pure static preview of the Verdict card design.
- **`Backend.zip`** contained a full `__MACOSX/` folder (23 `._*` AppleDouble resource-fork files) — pure zip artifact from compressing on macOS Finder, zero functional content. Safe to leave out of any future zip.
- **`engine.py`** has one deprecated *parameter* (not a whole file) worth noting: `velocity_window_minutes` (~line 1360) is marked `# deprecated, unused — velocity is now always 5/15/30min, see get_oi_velocity`. Harmless to leave, cheap to remove next time that function signature is touched.
- **`paper_trading.py`'s independent `LOT_SIZES`** (see §1) is the one live duplication risk found in this pass — it can silently drift from the shared `lot_sizes.py` the rest of the pipeline uses. Not urgent, but worth a single-source-of-truth fix.
- **`ml/inference.py`** was missing `import pandas as pd` despite `_infer()` calling `pd.DataFrame(...)` — the surrounding `try/except Exception` silently swallowed the resulting `NameError` on every call, so VirtualOI predicted a stale `0.0` forever instead of erroring loudly. Fixed by adding the import.
- **`risk/account_guard.py`'s position-field parsing** (`pnl_from_positions()`, `open_lots_from_positions()`, see §11) reads AngelOne SmartAPI's `position` response defensively across a few known field-name variants (`pnl`/`netpnl`/`realised`, `netqty`/`quantity`), but has NOT been verified against a real account's actual response shape yet. First live-trading session with this guard active should confirm the field names resolve (a "could not verify current open exposure" rejection message means they didn't) before relying on the exposure cap.

## 11. Backend — account-level risk guard (`risk/account_guard.py`)

Added to close the "no account-level risk limits" gap flagged during the
algo-trading-readiness review — per-order checks (`LIVE_MAX_LOTS_PER_ORDER`,
`LIVE_MAX_ORDERS_PER_MINUTE`, both in `ws_server_live.py`) already existed;
nothing tracked risk *across* orders over a trading day.

**Three guards, evaluated once per live order attempt, all persisted in
`runtime/cache/live_risk_guard.db` (survives a server restart):**

| Guard | Config (env var, default) | Trip condition |
|---|---|---|
| Daily max loss | `LIVE_MAX_DAILY_LOSS_RUPEES` (5000) | Account's total daily P&L (from AngelOne's own position book) ≤ -limit |
| Max open exposure | `LIVE_MAX_OPEN_LOTS` (5) | current open lots + this order's lots > limit — checked pre-trade, not after |
| Drawdown streak | `LIVE_MAX_CONSECUTIVE_DRAWDOWNS` (3) | daily P&L gets worse than its own running peak N checks in a row |

**Integration points in `ws_server_live.py`'s `_handle_place_order`:**
- `_ACCOUNT_GUARD = LiveAccountRiskGuard(LIVE_TRADING_KILL_SWITCH_FILE)` — instantiated once at module load, right next to the existing kill-switch file definition, so both mechanisms write to the **same** file (one kill switch, not two).
- Pre-trade: `is_tripped()` check, then (if a lot size was resolved) a fresh `smartapi_get_positions()` call feeds `open_lots_from_positions()` → `check_new_order()`.
- Post-fill (in the `finally` block, so it runs whether the order succeeded or failed): another `smartapi_get_positions()` call feeds `pnl_from_positions()` → `update_pnl()`.
- A trip touches `LIVE_TRADING_KILL_SWITCH_FILE` directly — it does **not** auto-clear on a new trading day even though the guard's own SQLite state resets daily; a human has to remove the file after reviewing what tripped it.

Tests: `backend/tests/test_account_guard.py` (16 cases — trip conditions, streak reset, persistence across instances, fail-closed parsing).

**Explicitly out of scope for this pass** (see the readiness roadmap this closed one item of): the strategy→execution bridge, a backtesting harness, broker session resilience, and position reconciliation are all still open.

## 12. Backend — strategy → execution bridge (`decision/auto_executor.py`)

Closes the "no automated decide→execute loop" gap from the algo-readiness
review — item #4 on that roadmap, built after §11's account_guard (item
#1) on purpose: automating order placement before account-level risk
limits exist is how accounts blow up. This is the piece that actually
makes the system an algo rather than a decision-support terminal —
everything upstream (`decision/decision_engine.py`) already computed a
bias/action every tick; nothing acted on it before this.

**Design — additive, not a replacement for the manual path:**
`AutoExecutor.evaluate()` is a pure function (`DecisionResult` dict +
symbol → `ExecutionDecision`) gating on: master switch
(`AUTO_STRATEGY_EXECUTION_ENABLED`, independent of `LIVE_TRADING_ENABLED`
— both must be true), `actionType` (single-leg only in v1 — `BUY_CE`,
`BUY_PE`, `SELL_CE`, `SELL_PE`; every multi-leg type and `WAIT` are
refused, not attempted), `conflictFlag`, `executeRecommended`,
`confidence` vs threshold, a present `suggestedStrike`, the account
guard's trip state, a per-symbol cooldown, and a per-symbol daily trade
cap. When cleared, `maybe_execute()` calls `submit_order_fn` — wired in
`ws_server_live.py` to `_submit_auto_order()`, which builds the same
payload shape a dashboard click sends and calls `_handle_place_order`
with `live=True, confirmed=True` filled in on the algo's behalf. Every
other check in that function (lot size, rate limit, account_guard
exposure/trip) runs exactly as it does for a human-submitted order — this
module only decides WHETHER and WHAT to submit, the existing safety
chain still gates HOW.

**Integration point:** `engine_loop()`, right after `LAST_PAYLOAD =
payload` — `payload.get("decision")` is handed to
`_AUTO_EXECUTOR.maybe_execute()` as a fire-and-forget `asyncio.create_task`
so a slow broker call can't delay that tick's own broadcast.

**Known limitation:** cooldown and per-symbol daily-trade-count state are
in-memory only (reset on restart) — unlike account_guard's SQLite state.
The persisted account-level guard is the real backstop across a restart;
cooldown/trade-count here are a secondary throttle layered on top, not
the primary safety mechanism. Worth persisting properly if
auto-execution actually goes live.

Tests: `backend/tests/test_auto_executor.py` (20 cases — gating logic,
action-type mapping, cooldown, daily cap, submit-failure handling).

**Still open after this pass:** backtesting harness (nothing has
validated `decision_engine.py`'s output against history before this
module can act on it live), broker session resilience, and position
reconciliation.

---

## 13. Frontend — metric authoritative sources (IA redesign step 4)

This is the write-down called for by `dashboard-redesign-proposal.md`
§4/§8: the agreed ownership model for every metric that appears in more
than one place on `DashboardPro.html`, produced *before* step 6's shared
metrics utility so that refactor centralizes an agreed architecture
instead of just relocating whatever each card happened to compute on its
own. Two categories below aren't simple owner/reference pairs — they're
**one shared computation, several legitimate aggregations of it** — and
should stay that way in the shared-utility refactor rather than being
forced into a single "authoritative value" cell each.

### PCR (Full Chain)
| | |
|---|---|
| Backend computation | `compute_total_pcr()` (`oi/chain_metrics.py`) → `engine.py`'s `self.total_pcr`, computed once per tick |
| Payload representations | `d.totalPCR` (raw float, `mTerminals_json.py`) and `dec.verdicts.pcr` (narrative string, `verdict_pcr()` in `signal_builder.py`) — **same source value**, formatted two ways for two different UI needs, not a duplication |
| Authoritative display | Decision Engine Tier-1 strip, narrative form (`vrd.pcr`) |
| Read-only references (raw float) | Conviction Gauge's "PCR Expansion" pillar (`d.totalPCR` vs `d.oiChgPCR`); Executive Dashboard's Market Health "OI Flow" score (blends `d.totalPCR`/`d.oiChgPCR`) |

### Range PCR (±N strikes)
| | |
|---|---|
| Computation | Frontend-only, `buildChainSummaryHtml()` (`chain-template.js`) — `totalPe/totalCe` over `getFilteredChain(d)`, the range-filtered chain. **Not backend-supplied** — a distinct metric from full-chain PCR above, not a re-derivation of it |
| Authoritative display | Option Chain Snapshot card, labeled "Range PCR (±N)" / "Range PCR Δ" (step 3) |
| Deliberately not shown elsewhere | OI Flow Summary explicitly dropped its own range/ATM PCR tile (see `oi-flow-view.js` comment) to avoid a third instance of this same number |

### Max Pain
| | |
|---|---|
| Backend computation | Single `d.maxPain` payload field |
| Authoritative display | Decision Engine Tier-1 strip |
| Read-only reference | Institutional F&O Simulator (annotates the payoff chart against it) |

### Gamma Flip Strike — two genuinely different computations sharing a name
| | |
|---|---|
| **Live** | `findGammaFlipStrike(greeks, atm)` over the live `d.greeks` array. Authoritative: Greeks/Net GEX Alerts card. Read-only reference: Conviction Gauge's "Gamma Flip (Regime Vote)" pillar (step 2) — same function, same live array |
| **Scenario-Adjusted** | `findGammaFlipStrike(simGEX, simSpot)` over the slider-adjusted `simGEX` array — Institutional F&O Simulator. **Not a reference to the live figure** — a legitimately different projection once spot/IV/dealer-bias sliders move away from their live defaults |

### Net GEX — same split as Gamma Flip, same reason
| | |
|---|---|
| **Live, whole-chain** | `totalGEX = greeks.reduce(...)` off the live payload. Authoritative: Greeks/Net GEX Alerts card ("Live, Whole-Chain", step 2). Read-only reference: Advanced Analytics' GEX Table ("Top \|GEX\| Strikes", step 2) — same per-strike `netGEX` values, just ranked/sliced, not recomputed |
| **Scenario-Adjusted** | Institutional F&O Simulator's Net GEX Profile chart ("Scenario-Adjusted", step 2) — each strike's `netGEX` multiplied by the slider-driven `ivRatio`/`vannaAdj` factors (`simulator-view.js`'s `simUpdate()`). A different number by design, not a duplicate of the live total |

### Smart-Money / Institutional Score (per-strike) — shared computation, three aggregations
| | |
|---|---|
| Shared per-strike scorer | `buildInstitutionalView(r, g)` (`chain-view-models.js`) — the one function computing OI dominance + writing-vs-buying signal + GEX sign per strike. All three cards below read this same function; none re-derives its own version |
| Shared badge/label helper | `smartMoneyBadge()` (`engines/smart-money.js`) — used by Institutional Activity Crux and Smart Money Ranking for their ACC/DIST/HEDGE/ROLL/RETAIL labels |
| Near-ATM Ledger | Institutional Activity Crux — banded near/far flagging vs. median OI, full visible chain |
| Whole-Chain Ranking | Smart Money Ranking (Advanced Analytics) — top 8 by \|ΔOI\| among institutional-flagged strikes |
| Aggregate Vote | Conviction Gauge's "Smart Money Lean (Aggregate Vote)" pillar (step 2) — chain-averaged single score |
| **Not part of this family** | Block-print detection (Vol/OI ratio spikes per strike) — Institutional F&O Simulator's Vol/OI Velocity panel. Historically shared the "Block Prints" label with Smart Money Lean despite measuring something unrelated; already renamed (see `conviction-gauge.js` comment) |

### Implication for step 6 (shared metrics utility)
The refactor should expose:
- **One function per genuinely-shared value** (full-chain PCR, max pain,
  live gamma flip, live net GEX, the per-strike institutional score) —
  each called once, everywhere else takes its return value.
- **Explicit `scope` parameters, not new functions,** for the legitimate
  variants (`range` vs `full` PCR; `live` vs `scenario` gamma
  flip/GEX) — collapsing these into one function that silently picks a
  scope would recreate the exact "same label, different number" problem
  step 3 just fixed for PCR.
- **No forced single owner** for the institutional-score aggregations —
  `buildInstitutionalView()` is already the correct shared primitive;
  the three cards' aggregation logic (banding / ranking / averaging)
  stays card-specific, only the per-strike input is centralized.
