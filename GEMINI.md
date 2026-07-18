# mTerminals Instructions

This is the central development instructions file for **mTerminals**, an advanced, live-streaming Futures & Options (F&O) dashboard. All developers and AI assistants must follow the guidelines and patterns defined in this document.

---

## 1. Project Overview

**mTerminals** is a high-performance F&O trading dashboard. It features:
- A real-time **Python backend** that fetches EOD and intraday tick-level data, solves Black-Scholes Greeks, tracks Option Interest (OI) buildup/velocity, and computes option chain parameters.
- An interactive, lightweight, flicker-free **Vanilla JavaScript frontend** offering specialized visualizations (Option Chain, GEX/DEX, OI Flow, GEX chart, Strategy Payoff, Paper Trading).

---

## 2. Directory Structure & File Mapping

```text
/Users/mo_re/mTerminals/
├── GEMINI.md                    # Core instructions (This file)
├── main.py                      # Basic entry point (Python backend)
├── ws_server_live.py            # Live WebSockets & HTTP server (aiohttp)
├── pyproject.toml               # Python project configuration (uv-based)
├── requirements.txt             # Pip dependencies
├── backend/                     # Backend modules and API clients
│   ├── decision_engine.py       # Core decision-maker converting EngineResult → DecisionResult
│   ├── engine.py                # Heavy option pricing/solver & single-pass computation engine
│   ├── market_api.py            # Core market data and index quotes retrieval
│   ├── mTerminals_json.py       # Exporter that writes final engine results to JSON
│   ├── nse_eod_fetch.py         # Pulls end-of-day data and checks if it's a trading day
│   ├── option_chain_json.py     # Option chain data structures and parsing
│   ├── paper_trading.py         # Local paper/virtual trading engine with custom orders
│   ├── smartapi_client.py       # Angel One SmartAPI client wrapper
│   ├── smartapi_feed_adapter.py # Aggregates tick feeds into a pipeline
│   ├── smartapi_ws_client.py    # Custom WebSockets stream for Angel One
│   └── virtual_oi_estimator.py  # Estimator for virtual/real-time OI buildup
└── frontend/                    # Frontend source files (Vanilla Web App)
    ├── DashboardPro.html        # Main dashboard layout
    ├── oi_dashboard.html        # Embedded OI dashboard view
    ├── option-chain.html        # Detailed strike-by-strike Option Chain ledger
    ├── dashboard.js             # Layout renderer & state coordinator
    ├── chain-views.js           # Renderers for option chain summaries
    ├── chart-renderer.js        # Canvas-based lightweight rendering
    ├── interaction-controller.js # UI interaction handling
    ├── ws-manager.js            # Frontend WebSocket manager
    └── [css files]              # Modular styles (theme.css, styles.css, utilities.css, etc.)
```

---

## 3. Technology Stack

### Backend (Python)
- **Runtime:** Python `>=3.13`
- **Core Dependencies:**
  - `numpy`: High-performance math and arrays.
  - `pandas`: Data structures and time-series history analysis.
  - `scipy`: Solving options parameters/Black-Scholes optimizations.
  - `websockets` & `aiohttp`: Async server and connection handling.
  - `orjson`: Lightning-fast JSON serialization.

### Frontend (Vanilla JS, HTML5, CSS3)
- **Frameworks:** **None**. Strictly Vanilla JS, raw CSS variables, and HTML.
- **Charts:** Custom-drawn HTML5 Canvas. **No Chart.js or D3.js** to avoid performance bottlenecks.
- **Layout & CSS:** Pure CSS. **No TailwindCSS**. Styles are structured in modular `.css` files (`theme.css`, `styles.css`, `utilities.css`, `oi-dashboard.css`).
- **Fonts:** Space Grotesk (sharper, display-friendly for headers/tickers), Inter (for text readability), and JetBrains Mono (for tabular code-friendly numbers).
- **Icons:** Inline SVG or raw characters. No FontAwesome or external icon libraries.

---

## 4. Key Engineering Standards

### Python & Backend Conventions
1. **Explicit Architecture Over Complexity:**
   - Prefer explicit composition. The codebase uses clean, typed modules (e.g., `engine.py` manages all option chain calculations; `decision_engine.py` translates calculated metrics into trade signals).
2. **Argv-Parsing and Module Imports:**
   - Some backend modules (e.g., `option_chain_json.py`) parse `sys.argv` at import time. When importing them in `ws_server_live.py` or other CLI scripts, wrap imports by temporarily overriding/restoring `sys.argv` (see `ws_server_live.py` lines 18-25).
3. **Data Integrity & Speed:**
   - Calculations must be heavily optimized. Ensure expensive operations (e.g., Max Pain, Option Chain Greeks, IV Solver) are done in single passes in `engine.py` and cached rather than computed redundantly.
   - Use `orjson` for fast serialization of large WebSocket payloads.

### JavaScript & Frontend Performance Guidelines
1. **Flicker-Free Rendering Pattern:**
   - **DOM Diffing Helper:** Never unconditionally overwrite `innerHTML` during live WebSocket ticks. Use `setHtmlIfChanged(el, html)` to compare the new content with `el.dataset.lastHtml` before touching the DOM.
   - **Canvas Sizing Helper:** Never resize canvas elements unless the layout changes. Resizing clears the 2D context and triggers repaints. Use `sizeCanvasIfChanged(canvas, wCss, hCss)` to manage canvas dimensions with the appropriate Device Pixel Ratio (DPR).
2. **State Management:**
   - State and interactions are encapsulated within modular classes (e.g., `MarketStore` or classes grouped by subsystem in `dashboard.js`). Pure functions (e.g., formatting utilities like `fmtN`) are stateless.
3. **Modal Frameworks:**
   - Modals (like the `oi-dashboard-modal`) are inline HTML templates toggled via CSS classes/visibility and iframes. No heavy modal libraries.

---

## 5. Development & Running Commands

### Running the Live Server
Start the real-time options calculator and WebSocket feeder:
```bash
python ws_server_live.py --symbol NIFTY --poll-seconds 2
```
Arguments:
- `--symbol`: Underlyings to track (e.g., `NIFTY`, `BANKNIFTY`).
- `--poll-seconds`: Delay between EOD or live data feeds.

### Serving the Frontend
Since the frontend consists of static files, any simple local HTTP server works:
```bash
python -m http.server 8000 --directory frontend
```
Navigate to: `http://localhost:8000/DashboardPro.html`

---

## 6. Guidelines for AI Assistants & Contributors
- **Respect Visuals and Aesthetics:** The frontend dashboard relies on clean fonts (Inter, Space Grotesk, JetBrains Mono) and crisp CSS styling. Always ensure new widgets or visual elements align with the designated layout, fonts, and CSS variables in `theme.css`.
- **Maintain Modular Frontend Scripts:** Avoid putting all logic in `dashboard.js`. Separate views/components into modular script files (e.g., `chain-views.js`, `chart-renderer.js`) as currently structured.
- **Do Not Add Bundlers:** Do not introduce Webpack, Vite, Babel, TailwindCSS, or Node-module style build pipelines in the frontend unless explicitly requested. It must remain immediately runnable in the browser.
- **Empirical Validation of Calculations:** If updating options pricing or financial calculations, ensure you test extreme market conditions (e.g., deep in-the-money or out-of-the-money options) to prevent math issues (like dividing by zero, negative IVs, or NaN propagation).
