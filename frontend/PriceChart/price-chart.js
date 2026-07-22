// ============================================================
// price-chart.js
// Extracted verbatim from dashboard.js (Task 1 modularization,
// step 1 — see master optimization prompt). No logic changed.
//
// Depends on globals defined in dashboard.js: $i(), sizeCanvasIfChanged(),
// fmtI(), sign(). Those are only called from inside methods that run after
// full page load (render/ensureMounted/_panelHtml/_attachHandlers/
// _refreshToolbarState), so load order relative to dashboard.js does not
// matter for correctness — but per the migration plan this file is loaded
// BEFORE dashboard.js in DashboardPro.html, right after chart-legend.js.
//
// `priceChart` is declared here with top-level `const` (not on `window`
// alone) so dashboard.js's existing bare `priceChart.addTick(...)` /
// `priceChart.render()` / `priceChart.ensureMounted()` / `priceChart.hydrate()`
// calls keep working unchanged — classic <script> tags share one global
// lexical scope, so a `const` declared in this file is visible to code in
// any script tag that runs after it. `window.priceChart` is also set,
// since a few call sites guard on `window.priceChart` before using it.
// ============================================================

// ══════════════════════════════════════════════════════
//  LIVE PRICE CHART — underlying/index price over time
// ══════════════════════════════════════════════════════
// Ticks come off d.spot on every WS message (see DataService.updateDashboard,
// where priceChart.addTick(...) is called). No historical bar feed exists —
// candles are built client-side by bucketing ticks, so the smallest usable
// candle width is bounded by how often the backend actually pushes spot.
// VWAP is sourced from d.allIndices' Volume/Value fields (cumulative
// session totals from NSE's allIndices endpoint) — Value/Volume on any
// given tick IS the exact running session VWAP already, so it's read
// directly per tick rather than reconstructed from per-tick deltas.
// BSE symbols (SENSEX/BANKEX) don't carry these fields yet — see
// fetch_bse_index_quote() in market_api.py — so VWAP is simply absent
// for those until that endpoint is extended.

const PRICE_CHART_SETTINGS_KEY = 'priceChartSettings.v1';

// Each entry's bucketMs is the actual candle period the button name
// implies (5M really does mean one candle = 5 minutes now — previously
// bucketMs was a display-density knob and `ms` was the range, which is
// backwards from every trading platform's convention and is why "5M" was
// showing a dozen 5-second candles instead of one 5-minute one).
//
// `ms` (visible window) is derived as bucketMs * candles rather than
// picked independently, so each range shows a consistent number of
// candles. Window sizes are generous relative to what the tick buffer
// actually has this early (live ticks since mount + a short hydrate()
// backfill) — that's fine, the chart still just fills in with whatever
// real data exists inside that window, same as before.
const PRICE_CHART_RANGES = {
  '1m':  { bucketMs: 60 * 1000,             candles: 60 },       // 1-minute candles,  last 1hr
  '5m':  { bucketMs: 5 * 60 * 1000,         candles: 75 },       // 5-minute candles,  last ~6.25hr (one NSE session)
  '15m': { bucketMs: 15 * 60 * 1000,        candles: 25 },       // 15-minute candles, last ~6.25hr (one NSE session)
  '1h':  { bucketMs: 60 * 60 * 1000,        candles: 32 },       // 1-hour candles,    last ~5 sessions
  '1d':  { bucketMs: 24 * 60 * 60 * 1000,   candles: 90 },       // daily candles,     last ~90 days
  'all': { bucketMs: 15 * 60 * 1000,        candles: Infinity }, // 15m candles, everything the buffer has
};
for (const cfg of Object.values(PRICE_CHART_RANGES)) {
  cfg.ms = cfg.candles === Infinity ? Infinity : cfg.bucketMs * cfg.candles;
}

// Bottom "window" shortcut bar (1D/5D/1M/3M/6M/1Y/5Y), TradingView-style —
// deliberately separate from PRICE_CHART_RANGES above. Those keys pick the
// *candle interval* (top toolbar, e.g. "5m"); these just move the visible
// window (via the existing zoom/pan state) without touching what interval
// is plotted, same as clicking "1M" on a real chart doesn't change the
// candle size. 'ALL' reuses the zoom-reset (null/null = full default
// window for the active interval) rather than a fixed span.
const PRICE_CHART_WINDOWS = [
  { key: '1D',  ms: 24 * 60 * 60 * 1000 },
  { key: '5D',  ms: 5 * 24 * 60 * 60 * 1000 },
  { key: '1M',  ms: 30 * 24 * 60 * 60 * 1000 },
  { key: '3M',  ms: 90 * 24 * 60 * 60 * 1000 },
  { key: '6M',  ms: 182 * 24 * 60 * 60 * 1000 },
  { key: '1Y',  ms: 365 * 24 * 60 * 60 * 1000 },
  { key: '5Y',  ms: 5 * 365 * 24 * 60 * 60 * 1000 },
  { key: 'ALL', ms: Infinity },
];

class PriceChartEngine {
  constructor(){
    this.mounted = false;
    this.settings = this._loadSettings();
    this._resizeDebounceTimer = null;
    this._resizeBound = () => {
      clearTimeout(this._resizeDebounceTimer);
      this._resizeDebounceTimer = setTimeout(() => this.render(), 100);
    };
    
    // Initialize component modules
    this.chartData = new ChartData(50000);
    this.chartRenderer = new ChartRenderer();
    this.indicatorEngine = new IndicatorEngine();
    this.historyLoader = new HistoryLoader(this.chartData, () => this.render());
    
    // Zoom/pan state
    this._zoomStart = null;
    this._zoomEnd = null;
    this._lastHydratedSymbol = null;
    
    // Render cache to avoid recalculating expensive operations
    this._renderCache = {
      tmap: null,
      seriesHash: null,
      windowStart: null,
      windowEnd: null,
      range: null
    };
    
    // Interaction controller (initialized later when canvas is available)
    this.interactionController = null;
  }

  _loadSettings(){
    const defaults = {
      type: 'line',            // 'line' | 'candle'
      range: '5m',             // key into PRICE_CHART_RANGES
      ohlcField: 'c',          // 'o'|'h'|'l'|'c' — which OHLC field line mode plots
      smaPeriods: [20],        // moving-average periods, in candles/points
      emaPeriods: [],
      showVwap: true,
      showGrid: true,
      glow: true,
      lineColor: '#339AF0',
      upColor: '#26A69A',      // TradingView-style teal/coral candle palette
      downColor: '#EF5350',
      vwapColor: '#FF8C42',
      showVolume: true,        // volume sub-panel under the price panel
    };
    try{
      const raw = localStorage.getItem(PRICE_CHART_SETTINGS_KEY);
      if(!raw) return defaults;
      return Object.assign(defaults, JSON.parse(raw));
    }catch(e){ return defaults; }
  }

  _saveSettings(){
    try{ localStorage.setItem(PRICE_CHART_SETTINGS_KEY, JSON.stringify(this.settings)); }
    catch(e){ /* storage unavailable — settings just won't persist */ }
  }

  addTick(price, t, vwap){
    this.chartData.addTick(price, t, vwap);
  }

  async hydrateRange(range, force){
    const symbol = (AppState.wsState && AppState.wsState.symbol) || 'default';
    await this.historyLoader.hydrateRange(range, force, symbol);
  }

  async hydrate(url){
    await this.historyLoader.hydrate(url);
  }

  _visibleTicks(){
    const cfg = PRICE_CHART_RANGES[this.settings.range] || PRICE_CHART_RANGES['5m'];
    return this.chartData.getVisibleTicks(cfg);
  }

  _aggregateCandles(ticks, bucketMs){
    return this.chartData.aggregateCandles(ticks, bucketMs);
  }

  _sma(values, period){
    return this.indicatorEngine.sma(values, period);
  }

  _ema(values, period){
    return this.indicatorEngine.ema(values, period);
  }

  // ── DOM ──────────────────────────────────────────────
  ensureMounted(){
    let host = document.getElementById('price-chart-section');
    // host.isConnected catches the case where #dashboard's innerHTML was
    // replaced wholesale (see scheduleRender's notYetBuilt/symbolChanged
    // branch) — the node with this id may still be sitting in memory via a
    // stale reference, but it's no longer attached to the visible document.
    if(host && host.isConnected){ this.mounted = true; return; }
    const createdHost = !host;
    if(createdHost){
      host = document.createElement('section');
      host.id = 'price-chart-section';
    }
    host.innerHTML = this._panelHtml();
    if(createdHost || !host.isConnected){
      const dashEl = $i('dashboard');
      if(dashEl) dashEl.insertBefore(host, dashEl.firstChild);
      else document.body.insertBefore(host, document.body.firstChild);
    }
    this._attachHandlers();
    if(!this._resizeAttached){
      window.addEventListener('resize', this._resizeBound);
      this._resizeAttached = true;
    }
    this.mounted = true;
  }

  // Styling for .pc-panel/.pc-toolbar/.pc-btn/etc. now lives in styles.css
  // (was runtime-injected here via a <style> tag — moved out so all CSS
  // lives in one file instead of being split across .js template literals).

  _panelHtml(){
    const s = this.settings;
    const rangeOpt = k => `<option value="${k}"${s.range===k?' selected':''}>${k.toUpperCase()}</option>`;
    const fieldOpt = (k,label) => `<option value="${k}"${s.ohlcField===k?' selected':''}>${label}</option>`;
    const typeBtn = (k,label) => `<button class="pc-btn pc-type-btn${s.type===k?' pc-active':''}" data-type="${k}">${label}</button>`;
    const winBtn = w => `<button class="pc-win-btn" data-win="${w.key}">${w.key}</button>`;
    return `
      <div class="pc-panel">
        <div class="pc-toolbar">
          <span class="pc-title">Live Price</span>
          <label class="pc-field pc-interval-field">
            <select id="pc-range-select">${['1m','5m','15m','1h','1d','all'].map(rangeOpt).join('')}</select>
          </label>
          ${typeBtn('line','Line')}
          ${typeBtn('candle','Candles')}
          <span class="pc-spacer"></span>
          <button class="pc-btn pc-settings-btn" id="pc-settings-toggle" title="Chart settings">⚙</button>
        </div>
        <div class="pc-toolbar pc-toolbar-sub" id="pc-settings-row" style="display:none;">
          <label class="pc-field">
            Field
            <select id="pc-field-select">${fieldOpt('o','Open')}${fieldOpt('h','High')}${fieldOpt('l','Low')}${fieldOpt('c','Close')}</select>
          </label>
          <label class="pc-field">
            <input type="checkbox" id="pc-sma-toggle" ${s.smaPeriods.length?'checked':''}/> SMA
            <input type="number" id="pc-sma-period" value="${s.smaPeriods[0]||20}" min="2" max="500"/>
          </label>
          <label class="pc-field">
            <input type="checkbox" id="pc-ema-toggle" ${s.emaPeriods.length?'checked':''}/> EMA
            <input type="number" id="pc-ema-period" value="${s.emaPeriods[0]||9}" min="2" max="500"/>
          </label>
          <label class="pc-field">
            <input type="checkbox" id="pc-vwap-toggle" ${s.showVwap?'checked':''}/> VWAP
          </label>
          <label class="pc-field">
            <input type="checkbox" id="pc-volume-toggle" ${s.showVolume?'checked':''}/> Volume
          </label>
          <label class="pc-field">
            <input type="checkbox" id="pc-grid-toggle" ${s.showGrid?'checked':''}/> Grid
          </label>
          <label class="pc-field">
            Color <input type="color" id="pc-line-color" value="${s.lineColor}"/>
          </label>
        </div>

        <div class="pc-ohlc-readout" id="pc-ohlc-readout">
          <span class="pc-ohlc-sym" id="pc-ohlc-sym">—</span>
          <span class="pc-ohlc-item">O<b id="pc-ohlc-o">—</b></span>
          <span class="pc-ohlc-item">H<b id="pc-ohlc-h">—</b></span>
          <span class="pc-ohlc-item">L<b id="pc-ohlc-l">—</b></span>
          <span class="pc-ohlc-item">C<b id="pc-ohlc-c">—</b></span>
          <span class="pc-ohlc-chg" id="pc-ohlc-chg">—</span>
        </div>

        <div class="pc-chart-wrap">
          <div class="pc-watermark" id="pc-watermark">—</div>
          <canvas id="price-chart-canvas" style="width:100%;display:block;" height="800"></canvas>
          <canvas id="price-chart-volume-canvas" style="width:100%;display:block;" height="120"></canvas>
          ${this._orderPanelHtml()}
        </div>

        <div class="pc-win-bar" id="pc-win-bar">
          ${PRICE_CHART_WINDOWS.map(winBtn).join('')}
          <button class="pc-btn pc-reset-btn" id="pc-reset-zoom" title="Reset zoom/pan to the default view">Reset</button>
          <span class="pc-spacer"></span>
          <span class="pc-footnote" id="pc-vwap-footnote">VWAP unavailable — this symbol's feed doesn't carry a volume field yet.</span>
        </div>
      </div>`;
  }

  // Floating quick-order panel, docked over the chart canvas (bottom-center).
  // Strike offsets are relative to ATM in step-of-1 units — resolving that
  // to an actual tradeable strike/symbol is the caller's job (see
  // window.pcPlaceOrder below), since this module has no option-chain data.
  _orderPanelHtml(){
    return `
      <div class="pc-order-panel" id="pc-order-panel">
        <div class="pc-order-head">
          <span class="pc-order-name" id="pc-order-symbol">—</span>
          <span class="pc-order-chg" id="pc-order-chg"></span>
        </div>
        <div class="pc-order-body">
          <div class="pc-seg" id="pc-order-side">
            <button class="pc-active pc-buy" data-side="BUY">B</button>
            <button data-side="SELL">S</button>
          </div>
          <div class="pc-seg" id="pc-order-opt">
            <button class="pc-active" data-opt="CALL">CALL</button>
            <button data-opt="PUT">PUT</button>
          </div>
          <select id="pc-order-strike">
            <option value="0">ATM</option>
            <option value="-1">ITM 1</option>
            <option value="-2">ITM 2</option>
            <option value="1">OTM 1</option>
            <option value="2">OTM 2</option>
          </select>
          <input class="pc-order-qty" id="pc-order-lots" type="number" value="1" min="1" />
          <span class="pc-order-lbl">Lots</span>
          <select id="pc-order-type">
            <option value="LIMIT">LIMIT</option>
            <option value="MARKET">MARKET</option>
          </select>
          <input class="pc-order-price" id="pc-order-price" type="text" value="" />
          <button class="pc-order-submit" id="pc-order-submit">BUY</button>
          <button class="pc-btn" id="pc-order-sltgt">SL/TGT</button>
        </div>
      </div>`;
  }

  _attachHandlers(){
    const panel = document.getElementById('price-chart-section');
    if(!panel) return;
    const rangeSelect = $i('pc-range-select');
    if(rangeSelect){
      rangeSelect.onchange = () => { this.settings.range = rangeSelect.value; this._zoomStart = this._zoomEnd = null; this._saveSettings(); this.hydrateRange(rangeSelect.value); this.render(true); };
    }
    const fieldSelect = $i('pc-field-select');
    if(fieldSelect){
      fieldSelect.onchange = () => { this.settings.ohlcField = fieldSelect.value; this._saveSettings(); this.render(); };
    }
    panel.querySelectorAll('.pc-type-btn').forEach(b=>{
      b.onclick = () => { this.settings.type = b.dataset.type; this._zoomStart = this._zoomEnd = null; this._saveSettings(); this._refreshToolbarState(); this.render(true); };
    });
    const smaToggle = $i('pc-sma-toggle'), smaPeriod = $i('pc-sma-period');
    const emaToggle = $i('pc-ema-toggle'), emaPeriod = $i('pc-ema-period');
    const vwapToggle = $i('pc-vwap-toggle');
    const gridToggle = $i('pc-grid-toggle');
    const lineColor = $i('pc-line-color');
    const syncSma = () => { this.settings.smaPeriods = smaToggle.checked ? [parseInt(smaPeriod.value,10)||20] : []; this._saveSettings(); this.render(); };
    const syncEma = () => { this.settings.emaPeriods = emaToggle.checked ? [parseInt(emaPeriod.value,10)||9] : []; this._saveSettings(); this.render(); };
    if(smaToggle){ smaToggle.onchange = syncSma; smaPeriod.onchange = syncSma; }
    if(emaToggle){ emaToggle.onchange = syncEma; emaPeriod.onchange = syncEma; }
    if(vwapToggle) vwapToggle.onchange = () => { this.settings.showVwap = vwapToggle.checked; this._saveSettings(); this.render(); };
    if(gridToggle) gridToggle.onchange = () => { this.settings.showGrid = gridToggle.checked; this._saveSettings(); this.render(); };
    if(lineColor) lineColor.onchange = () => { this.settings.lineColor = lineColor.value; this._saveSettings(); this.render(); };
    const volumeToggle = $i('pc-volume-toggle');
    if(volumeToggle) volumeToggle.onchange = () => { this.settings.showVolume = volumeToggle.checked; this._saveSettings(); this.render(); };

    // Collapsed-by-default settings row (SMA/EMA/VWAP/grid/color) — the
    // gear icon toggles it, keeping the main toolbar as uncluttered as a
    // real trading platform's chart header instead of a wall of controls.
    const settingsToggle = $i('pc-settings-toggle');
    const settingsRow = $i('pc-settings-row');
    if(settingsToggle && settingsRow){
      settingsToggle.onclick = () => {
        const open = settingsRow.style.display !== 'none';
        settingsRow.style.display = open ? 'none' : 'flex';
        settingsToggle.classList.toggle('pc-active', !open);
      };
    }

    // ── Bottom window-shortcut bar (1D/5D/1M/3M/6M/1Y/5Y/ALL) ──
    // Purely adjusts the visible zoom window, independent of the candle
    // interval selected up top — same division of labor as a real chart's
    // "5m" interval dropdown vs its date-range shortcuts underneath.
    const winBar = $i('pc-win-bar');
    if(winBar){
      winBar.querySelectorAll('.pc-win-btn').forEach(b => {
        b.onclick = () => {
          winBar.querySelectorAll('.pc-win-btn').forEach(x => x.classList.remove('pc-active'));
          b.classList.add('pc-active');
          const w = PRICE_CHART_WINDOWS.find(x => x.key === b.dataset.win);
          if(!w) return;
          if(w.ms === Infinity){
            this._zoomStart = null; this._zoomEnd = null;
          } else {
            const now = Date.now();
            this._zoomStart = now - w.ms;
            this._zoomEnd = now;
          }
          // Wide windows need real history beyond the tick buffer — make
          // sure the 'all' bar set has been fetched so zooming out actually
          // has data to show instead of empty space.
          if(w.ms === Infinity || w.ms > (PRICE_CHART_RANGES[this.settings.range]||{}).ms){
            this.hydrateRange('all');
          }
          this.render();
        };
      });
    }

    const resetZoomBtn = $i('pc-reset-zoom');
    if(resetZoomBtn){
      resetZoomBtn.onclick = () => {
        this._zoomStart = null; this._zoomEnd = null;
        if(winBar) winBar.querySelectorAll('.pc-win-btn').forEach(x => x.classList.remove('pc-active'));
        this.render();
      };
    }

    // ── Zoom (wheel) / pan (drag) / reset (double-click) ──
    // Uses this._lastRenderCtx (set at the end of render()) to convert
    // mouse pixel positions into chart time coordinates — avoids
    // recomputing the window/series logic outside render().
    const canvas = document.getElementById('price-chart-canvas');
    if(canvas && !canvas._pcZoomWired){
      canvas._pcZoomWired = true;
      canvas.style.cursor = 'grab';

      canvas.addEventListener('wheel', (e) => {
        const rc = this._lastRenderCtx;
        if(!rc) return;
        e.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const vAtCursor = rc.vWindowStart + ((mouseX - rc.PAD.l) / rc.PW) * rc.vSpan;
        const tAtCursor = rc.tmap.toReal(vAtCursor);
        const curStart = this._zoomStart != null ? this._zoomStart : rc.windowStart;
        const curEnd = this._zoomEnd != null ? this._zoomEnd : rc.windowStart + rc.span;
        const zoomFactor = e.deltaY < 0 ? 0.85 : (1 / 0.85); // wheel up = zoom in
        const dataSpan = Math.max(rc.minSpan, rc.dataMaxT - rc.dataMinT);
        let newSpan = Math.max(rc.minSpan, Math.min((curEnd - curStart) * zoomFactor, dataSpan));
        const ratio = (curEnd > curStart) ? (tAtCursor - curStart) / (curEnd - curStart) : 0.5;
        let newStart = tAtCursor - ratio * newSpan;
        let newEnd = newStart + newSpan;
        if(newStart < rc.dataMinT){ newStart = rc.dataMinT; newEnd = newStart + newSpan; }
        if(newEnd > rc.dataMaxT){ newEnd = rc.dataMaxT; newStart = newEnd - newSpan; }
        this._zoomStart = newStart; this._zoomEnd = newEnd;
        this.render();
      }, { passive: false });

      canvas.onmousedown = (e) => {
        const rc = this._lastRenderCtx;
        if(!rc) return;
        this._drag = {
          startX: e.clientX,
          winStart: this._zoomStart != null ? this._zoomStart : rc.windowStart,
          winEnd: this._zoomEnd != null ? this._zoomEnd : rc.windowStart + rc.span,
        };
        canvas.style.cursor = 'grabbing';
      };
      canvas.ondblclick = () => { this._zoomStart = null; this._zoomEnd = null; this.render(); };

      // Attached once per instance (not per canvas) — canvas gets looked
      // up fresh each call so these keep working across a canvas swap
      // instead of being re-added and piling up on window every rerender.
      if(!this._panWired){
        this._panWired = true;
        window.addEventListener('mousemove', (e) => {
          const drag = this._drag;
          if(!drag) return;
          const rc = this._lastRenderCtx;
          if(!rc) return;
          const dxPx = e.clientX - drag.startX;
          const spanMs = drag.winEnd - drag.winStart;
          const dtMs = -(dxPx / rc.PW) * spanMs;
          let newStart = drag.winStart + dtMs;
          let newEnd = drag.winEnd + dtMs;
          if(newStart < rc.dataMinT){ newStart = rc.dataMinT; newEnd = newStart + spanMs; }
          if(newEnd > rc.dataMaxT){ newEnd = rc.dataMaxT; newStart = newEnd - spanMs; }
          this._zoomStart = newStart; this._zoomEnd = newEnd;
          this.render();
        });
        window.addEventListener('mouseup', () => {
          if(this._drag){
            this._drag = null;
            const liveCanvas = document.getElementById('price-chart-canvas');
            if(liveCanvas) liveCanvas.style.cursor = 'grab';
          }
        });
      }

      // ── Crosshair ── separate from the drag/pan handler above: drag only
      // starts once the mouse is actually held down (mousedown), so a plain
      // hover here is unambiguous. Throttled to one render per animation
      // frame so fast mouse movement doesn't queue up a redraw backlog.
      canvas.onmousemove = (e) => {
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        // Only trigger render if position actually changed
        if(!this._hover || this._hover.x !== x || this._hover.y !== y){
          this._hover = { x, y };
          if(this._hoverRaf) return;
          this._hoverRaf = requestAnimationFrame(() => { this._hoverRaf = null; this.render(); });
        }
      };
      canvas.onmouseleave = () => {
        if(!this._hover) return; // Already cleared
        this._hover = null;
        if(this._hoverRaf) return;
        this._hoverRaf = requestAnimationFrame(() => { this._hoverRaf = null; this.render(); });
      };
    }

    this._attachOrderPanelHandlers();
  }

  // ── Quick-order panel ────────────────────────────────
  // Submission is delegated to window.pcPlaceOrder(payload) so this module
  // stays decoupled from the WS/paper-trading layer. Wire that global to
  // your existing execute path (e.g. the same place_order message
  // ptExecuteLeg() sends) — if it isn't defined yet, submit just logs and
  // no-ops, so the panel is safe to ship ahead of that wiring.
  _attachOrderPanelHandlers(){
    const panel = $i('pc-order-panel');
    if(!panel || panel._pcOrderWired) return;
    panel._pcOrderWired = true;

    this._orderState = { side: 'BUY', opt: 'CALL' };

    const sideSeg = $i('pc-order-side');
    sideSeg.querySelectorAll('button').forEach(b => {
      b.onclick = () => {
        sideSeg.querySelectorAll('button').forEach(x => x.classList.remove('pc-active','pc-buy','pc-sell'));
        b.classList.add('pc-active', b.dataset.side === 'BUY' ? 'pc-buy' : 'pc-sell');
        this._orderState.side = b.dataset.side;
        this._refreshOrderSubmit();
      };
    });

    const optSeg = $i('pc-order-opt');
    optSeg.querySelectorAll('button').forEach(b => {
      b.onclick = () => {
        optSeg.querySelectorAll('button').forEach(x => x.classList.remove('pc-active'));
        b.classList.add('pc-active');
        this._orderState.opt = b.dataset.opt;
      };
    });

    const priceInput = $i('pc-order-price');
    const orderType = $i('pc-order-type');
    priceInput.oninput = () => this._refreshOrderSubmit();
    orderType.onchange = () => {
      // MARKET orders don't take a limit price — disable but keep the last
      // value around in case the user switches back to LIMIT.
      priceInput.disabled = orderType.value === 'MARKET';
      this._refreshOrderSubmit();
    };

    $i('pc-order-submit').onclick = () => {
      const payload = {
        side: this._orderState.side,
        optType: this._orderState.opt,
        strikeOffset: parseInt($i('pc-order-strike').value, 10),
        lots: parseInt($i('pc-order-lots').value, 10) || 1,
        orderType: orderType.value,
        price: orderType.value === 'MARKET' ? null : parseFloat(priceInput.value) || null,
      };
      if(typeof window.pcPlaceOrder === 'function'){
        window.pcPlaceOrder(payload);
      } else {
        console.warn('[priceChart] window.pcPlaceOrder is not wired — order not sent:', payload);
      }
      const btn = $i('pc-order-submit');
      const prevLabel = btn.textContent;
      btn.textContent = 'Sent ✓';
      setTimeout(() => { this._refreshOrderSubmit(); }, 900);
    };

    this._refreshOrderSubmit();
  }

  _refreshOrderSubmit(){
    const btn = $i('pc-order-submit');
    if(!btn) return;
    const priceInput = $i('pc-order-price');
    const orderType = $i('pc-order-type');
    const side = this._orderState ? this._orderState.side : 'BUY';
    btn.classList.toggle('pc-sell', side === 'SELL');
    const priceTxt = (orderType && orderType.value === 'MARKET') ? 'MKT' : (priceInput ? priceInput.value : '');
    btn.textContent = priceTxt ? `${side} @ ${priceTxt}` : side;
  }

  // Called from render() so the panel's header and default LIMIT price
  // track the live feed instead of sitting stale at whatever they were on
  // mount.
  _syncOrderPanel(lastTick){
    const nameEl = $i('pc-order-symbol');
    const chgEl = $i('pc-order-chg');
    const priceInput = $i('pc-order-price');
    if(!nameEl || !lastTick) return;
    nameEl.textContent = fmtI(lastTick.p);
    if(chgEl && lastTick.chg != null){
      chgEl.textContent = `${sign(lastTick.chg)}${fmtI(Math.abs(lastTick.chg))}`;
      chgEl.classList.toggle('pc-neg', lastTick.chg < 0);
      chgEl.classList.toggle('pc-pos', lastTick.chg >= 0);
    }
    // Only auto-fill the price while the user hasn't started editing it,
    // so a live tick doesn't yank the field out from under someone mid-type.
    if(priceInput && !priceInput.dataset.touched){
      priceInput.value = fmtI(lastTick.p);
      if(!priceInput._pcTouchWired){
        priceInput._pcTouchWired = true;
        priceInput.addEventListener('input', () => { priceInput.dataset.touched = '1'; }, { once: true });
      }
    }
  }

  // Maps real timestamps to a "virtual" compressed timeline: any gap
  // between consecutive points bigger than gapThreshold (e.g. the
  // overnight/weekend gap between NSE sessions) is squeezed down to
  // gapCap of virtual width, however many real hours it actually spans.
  // Normal in-session spacing passes through 1:1. This is what keeps a
  // multi-day '1h'/'1d'/'all' view from wasting most of its width on
  // closed-market dead space the way a plain linear time axis does.
  // toReal() is the inverse, used by the wheel/drag handlers to convert a
  // mouse pixel position back into a real timestamp.
  _buildTimeMap(points, gapThreshold, gapCap){
    return this.chartRenderer.buildTimeMap(points, gapThreshold, gapCap);
  }

  _refreshToolbarState(){
    const panel = document.getElementById('price-chart-section');
    if(!panel) return;
    panel.querySelectorAll('.pc-type-btn').forEach(b=>b.classList.toggle('pc-active', b.dataset.type===this.settings.type));
  }

  // Populates the #pc-ohlc-readout strip (O/H/L/C + change) from a given
  // point, or resets every field back to the '—' placeholder when called
  // with null (e.g. the no-visible-data early-return path in render()
  // below). Accepts either a candle-shaped point ({t,o,h,l,c}) or a
  // line-mode point ({t,p}), since render() plots either depending on
  // this.settings.type — for the latter, o/h/l/c all collapse to the
  // same value `p`.
  _renderOhlcReadout(point, idx){
    const oEl = $i('pc-ohlc-o'), hEl = $i('pc-ohlc-h'), lEl = $i('pc-ohlc-l'),
          cEl = $i('pc-ohlc-c'), chgEl = $i('pc-ohlc-chg');
    if(!point){
      if(oEl) oEl.textContent = '—';
      if(hEl) hEl.textContent = '—';
      if(lEl) lEl.textContent = '—';
      if(cEl) cEl.textContent = '—';
      if(chgEl){ chgEl.textContent = '—'; chgEl.className = 'pc-ohlc-chg'; }
      return;
    }
    const fmt = v => (v == null ? '—' : v.toLocaleString('en-IN', {maximumFractionDigits:2}));
    const o = point.o != null ? point.o : point.p;
    const h = point.h != null ? point.h : point.p;
    const l = point.l != null ? point.l : point.p;
    const c = point.c != null ? point.c : point.p;
    if(oEl) oEl.textContent = fmt(o);
    if(hEl) hEl.textContent = fmt(h);
    if(lEl) lEl.textContent = fmt(l);
    if(cEl) cEl.textContent = fmt(c);
    if(chgEl){
      if(o != null && c != null){
        const chg = c - o;
        const pct = o !== 0 ? (chg / o) * 100 : 0;
        const sign = chg > 0 ? '+' : '';
        chgEl.textContent = `${sign}${fmt(chg)} (${sign}${pct.toFixed(2)}%)`;
        chgEl.className = 'pc-ohlc-chg ' + (chg > 0 ? 'pc-up' : chg < 0 ? 'pc-down' : '');
      } else {
        chgEl.textContent = '—';
        chgEl.className = 'pc-ohlc-chg';
      }
    }
  }

  // ── RENDER ───────────────────────────────────────────
  render(forceResize){
    this.ensureMounted();
    const canvas = document.getElementById('price-chart-canvas');
    if(!canvas) return;
    
    // Early return if no data
    const lastTick = this.chartData.getLastTick();
    if(!lastTick){
      this._syncOrderPanel(null);
      return;
    }

    this._syncOrderPanel(lastTick);
    const watermarkEl = $i('pc-watermark');
    const symEl = $i('pc-ohlc-sym');
    const symText = (AppState.wsState && AppState.wsState.symbol) || '';
    if(watermarkEl) watermarkEl.textContent = symText || '—';
    if(symEl) symEl.textContent = symText || '—';

    // Scrip switched — fetch that scrip's history for the active range.
    // Previously nothing called hydrateRange() on a symbol change at all,
    // so every scrip other than whichever was active when you last picked
    // a range/window button just sat on tick-bucketed data with no real
    // candle history.
    if(symText && symText !== this._lastHydratedSymbol){
      this._lastHydratedSymbol = symText;
      this._renderCache.tmap = null; // Invalidate cache on symbol change
      this.chartData.clearForSymbolChange(symText); // Free memory for old symbol
      this.hydrateRange(this.settings.range);
    }


    const visible = this._visibleTicks();
    if(!visible.length){
      const W0 = canvas.parentElement.clientWidth - 24, H0 = Math.max(800, canvas.parentElement.clientHeight);
      const ctx = sizeCanvasIfChanged(canvas, W0, H0);
      ctx.clearRect(0,0,W0,H0);
      this._renderOhlcReadout(null, null);
      return;
    }

    const cfg = PRICE_CHART_RANGES[this.settings.range] || PRICE_CHART_RANGES['5m'];
    const isDark = window.matchMedia('(prefers-color-scheme:dark)').matches;
    const C = {
      grid: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
      axisLbl: isDark ? '#6C757D' : '#868E96',
      line: this.settings.lineColor,
      lineGlow: isDark ? 'rgba(51,154,240,0.5)' : 'rgba(51,154,240,0.35)',
      up: this.settings.upColor,
      down: this.settings.downColor,
      sma: '#FFD43B',
      ema: '#B197FC',
      vwap: this.settings.vwapColor,
    };

    const W0 = canvas.parentElement.clientWidth - 24, H0 = Math.max(800, canvas.parentElement.clientHeight);
    const ctx = sizeCanvasIfChanged(canvas, W0, H0);
    const W = W0, H = H0;
    const PAD = { l: 54, r: 12, t: 12, b: 22 };
    const PW = W - PAD.l - PAD.r, PH = H - PAD.t - PAD.b;

    ctx.clearRect(0,0,W,H);

    let series, values;
    const symbol = (AppState.wsState && AppState.wsState.symbol) || '';
    const bars = this.chartData.getHistoryBars(this.settings.range, symbol);
    const _now = Date.now();
    // Bounds of data actually available for this range — earliest real bar,
    // or earliest tick if no bars have loaded yet. Zoom/pan are clamped to
    // this so you can't drag/scroll into empty space.
    const dataMinT = (bars && bars.length) ? bars[0].t : (visible.length ? visible[0].t : _now - 60000);
    const dataMaxT = _now;
    const minSpan = Math.max(cfg.bucketMs * 5, 15000);

    // Default (non-zoomed) window for the active range — same rolling-window
    // sizing as before zoom existed. 'all' defaults to the full data span;
    // finite ranges default to their own fixed window, but (unlike before)
    // can now be zoomed OUT past it up to dataMinT, since the backend
    // already fetches several days of real bars per range.
    const defaultStart = cfg.ms === Infinity ? dataMinT : Math.max(dataMinT, _now - cfg.ms);
    const defaultEnd = _now;

    // Zoom override from wheel/drag, clamped to data bounds + minSpan.
    let windowStart = defaultStart, windowEnd = defaultEnd;
    if(this._zoomStart != null && this._zoomEnd != null){
      windowStart = Math.max(dataMinT, Math.min(this._zoomStart, dataMaxT - minSpan));
      windowEnd = Math.min(dataMaxT, Math.max(this._zoomEnd, windowStart + minSpan));
    }
    const span = Math.max(1, windowEnd - windowStart);

    const _fieldKey = ({o:'o',h:'h',l:'l',c:'c'})[this.settings.ohlcField] || 'c';

    if(bars && bars.length){
      // Real SmartAPI bars for this range. Splice in any live ticks newer
      // than the last bar as one extra forming candle, so the chart stays
      // real-time instead of freezing at the last historical bar until the
      // next full re-hydrate.
      const merged = this.chartData.mergeLiveBars(bars, visible, cfg.bucketMs, windowStart, windowEnd);
      if(merged){
        if(this.settings.type === 'candle'){
          series = merged;
        } else {
          // Line mode reads t.p per point — alias the selected OHLC field
          // (open/high/low/close) onto .p rather than rewriting the
          // line-draw path below.
          series = merged.map(c => ({ t: c.t, p: c[_fieldKey], vw: null }));
        }
        values = merged.map(c=>c[_fieldKey]);
      }
    } else if(this.settings.type === 'candle'){
      // bucketMs is now a real, fixed candle period (60s minimum across
      // every range) — always well above the feed's ~5-6s tick gap, so
      // there's no more need to float the bucket width off the median
      // tick interval the way the old density-based ranges required.
      series = this._aggregateCandles(visible.filter(tk => tk.t >= windowStart && tk.t <= windowEnd), cfg.bucketMs);
      values = series.map(c=>c.c);
    } else {
      series = visible.filter(tk => tk.t >= windowStart && tk.t <= windowEnd);
      values = series.map(t=>t.p);
    }
    if(!series.length) return;

    // ── OHLC readout ── defaults to the whole visible window's O/H/L and
    // the latest close; overridden by whichever candle/point the crosshair
    // is nearest to while hovering. hoverIdx is resolved properly once
    // xScale exists further down — this first pass just seeds "no hover"
    // so a mouseleave (this._hover === null) always falls back cleanly.
    let hoverIdx = -1;

    // Y-axis is fit to the most recent candles rather than the whole
    // visible window — on wider ranges (e.g. a full session for '5m'),
    // fitting to the entire window let big early-session swings dictate
    // the scale, flattening small recent moves (a 4-5pt move became
    // invisible against a 2000+pt session range). Fitting to a recent
    // tail keeps the scale reactive to what's actually happening now;
    // older candles outside this tail may draw clipped, which is the
    // expected trade-off (same as a chart's "auto-scale visible" mode).
    const FIT_LOOKBACK = 40;
    const fitStart = Math.max(0, series.length - FIT_LOOKBACK);
    const fitSeries = series.slice(fitStart);
    const fitValues = values.slice(fitStart);
    const yMin = Math.min(...(this.settings.type==='candle'?fitSeries.map(c=>c.l):fitValues));
    const yMax = Math.max(...(this.settings.type==='candle'?fitSeries.map(c=>c.h):fitValues));
    const padY = (yMax - yMin) * 0.1 || 1;
    const y0 = yMin - padY, y1 = yMax + padY;

    // Fully compress non-trading gaps (overnight/weekend) to zero width —
    // consecutive sessions sit directly back-to-back, same as any real
    // trading platform's chart. Anything bigger than ~4 candle-widths
    // (min 20 min) counts as "not trading hours" and gets collapsed.
    const GAP_THRESHOLD = Math.max(cfg.bucketMs * 1.5, 20 * 60 * 1000);
    const GAP_CAP = 0;
    // Treat the window's own start/end as points in the same map as the
    // candles — this is what makes pre-market dead space (windowStart up
    // to the first real candle, e.g. a 6.25hr lookback window starting
    // before 09:15 open) collapse exactly like an overnight gap does,
    // instead of only compressing gaps *between* candles and leaving the
    // leading/trailing edges as raw uncompressed real time.
    
    // Cache time map to avoid rebuilding on every render
    const seriesHash = series.length + ':' + series[0]?.t + ':' + series[series.length-1]?.t + ':' + windowStart + ':' + windowEnd;
    let tmap = this._renderCache.tmap;
    if(!tmap || this._renderCache.seriesHash !== seriesHash || this._renderCache.range !== this.settings.range){
      const mapPoints = series.slice();
      if(mapPoints.length && windowStart < mapPoints[0].t) mapPoints.unshift({ t: windowStart });
      if(mapPoints.length && windowEnd > mapPoints[mapPoints.length - 1].t) mapPoints.push({ t: windowEnd });
      tmap = this._buildTimeMap(mapPoints, GAP_THRESHOLD, GAP_CAP);
      this._renderCache.tmap = tmap;
      this._renderCache.seriesHash = seriesHash;
      this._renderCache.range = this.settings.range;
    }
    const vWindowStart = tmap.toVirtual(windowStart);
    const vWindowEnd = tmap.toVirtual(windowEnd);
    const vSpan = Math.max(1, vWindowEnd - vWindowStart);

    const xScale = t => PAD.l + ((tmap.toVirtual(t) - vWindowStart) / vSpan) * PW;
    const yScale = v => PAD.t + PH - ((v - y0)/(y1 - y0)) * PH;

    // Use ChartRenderer for main rendering
    const zoomState = { windowStart, windowEnd };
    const dataBounds = { minT: dataMinT, maxT: dataMaxT };
    const renderResult = this.chartRenderer.render(canvas, series, values, this.settings, cfg, zoomState, dataBounds);
    
    // Draw indicator overlays
    if(renderResult && this.settings.smaPeriods.length){
      this.chartRenderer.drawOverlay(renderResult.ctx, series, values, this.settings.smaPeriods, C.sma, renderResult.xScale, renderResult.yScale, this.indicatorEngine.sma.bind(this.indicatorEngine));
    }
    if(renderResult && this.settings.emaPeriods.length){
      this.chartRenderer.drawOverlay(renderResult.ctx, series, values, this.settings.emaPeriods, C.ema, renderResult.xScale, renderResult.yScale, this.indicatorEngine.ema.bind(this.indicatorEngine));
    }

    // VWAP overlay
    let lastVwap = null;
    if(renderResult && this.settings.showVwap){
      this.chartRenderer.drawVwap(renderResult.ctx, series, renderResult.xScale, renderResult.yScale, C.vwap);
      lastVwap = series.find(pt => pt.vw != null)?.vw;
    }
    
    const footnote = document.getElementById('pc-vwap-footnote');
    if(footnote){
      footnote.textContent = lastVwap != null
        ? `VWAP ${lastVwap.toLocaleString('en-IN', {maximumFractionDigits:2})}`
        : "VWAP unavailable — this symbol's feed doesn't carry a volume field yet.";
    }
  }
}

const priceChart = new PriceChartEngine();
window.priceChart = priceChart;
