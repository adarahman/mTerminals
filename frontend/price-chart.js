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

class PriceChartEngine {
  constructor(){
    this.ticks = []; // {t: epoch ms, p: price}
    this.MAX_TICKS = 50000;
    this.mounted = false;
    this.settings = this._loadSettings();
    this._resizeBound = () => this.render();
    // Real OHLCV bars from SmartAPI, keyed by PRICE_CHART_RANGES key —
    // separate from `ticks` because ticks carry no volume and (until a
    // page has been open a long time) can't reconstruct real intraday
    // high/low or genuine long-range history the way SmartAPI's own
    // candles can. Populated by hydrateRange(); render() prefers this
    // over tick-bucketing whenever it has bars for the active range.
    this.historyBars = {};
    this._hydratingRanges = new Set();
    // Zoom/pan state — null means "use the range's default window" (today's
    // behavior). Set by wheel (zoom) and drag (pan) handlers in
    // _attachHandlers(); reset whenever range/type changes since a
    // different range implies different underlying data/window.
    this._zoomStart = null;
    this._zoomEnd = null;
    // Populated at the end of each render() with the pixel<->time mapping
    // that render actually used, so wheel/drag handlers (which run outside
    // render) can convert mouse coordinates without recomputing everything.
    this._lastRenderCtx = null;
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
      upColor: '#20C997',
      downColor: '#FF6B6B',
      vwapColor: '#FF8C42',
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
    price = parseFloat(price);
    if(!price || isNaN(price)) return;
    const now = Date.now();
    t = t || now;
    const vw = (vwap != null && isFinite(vwap)) ? vwap : null;
    const last = this.ticks[this.ticks.length - 1];
    if(last && last.t === t){
      if(last.p === price && last.vw === vw) return; // true no-op — exact repeat, nothing changed
      // Price (or vwap) actually moved even though the incoming timestamp
      // didn't — this happens when the backend timestamp has coarser
      // (e.g. whole-second) resolution than the tick rate. Previously this
      // branch just overwrote last.p/last.vw in place, so the point never
      // advanced and the chart only visibly moved once per timestamp tick.
      // Nudge the timestamp forward using client arrival time so the new
      // price still lands as its own point.
      t = Math.max(now, last.t + 1);
    }
    this.ticks.push({ t, p: price, vw });
    if(this.ticks.length > this.MAX_TICKS) this.ticks.shift();
  }

  // Fetches real OHLCV history for one PRICE_CHART_RANGES key from the
  // backend's /api/history endpoint (SmartAPI-sourced, full range —
  // 'all' means Angel One's actual daily-candle limit, not just whatever
  // ticks happened to accumulate client-side). Cached per range so
  // switching back and forth doesn't re-hit SmartAPI's rate-limited
  // historical endpoint every click; pass force=true to bypass the cache
  // (e.g. a manual refresh action, if one gets added later).
  async hydrateRange(range, force){
    if(!force && this.historyBars[range]) { this.render(); return; }
    if(this._hydratingRanges.has(range)) return;
    this._hydratingRanges.add(range);
    try{
      const res = await fetch(`/api/history?range=${encodeURIComponent(range)}`);
      if(!res.ok){ console.warn('[priceChart] hydrateRange failed:', res.status, res.statusText, range); return; }
      const rows = await res.json(); // expected: [{t,o,h,l,c,v}, ...] oldest→newest
      if(!Array.isArray(rows)) return;
      const bars = rows
        .map(r => ({
          t: Number(r.t), o: parseFloat(r.o), h: parseFloat(r.h),
          l: parseFloat(r.l), c: parseFloat(r.c),
          v: (r.v != null && isFinite(r.v)) ? Number(r.v) : null,
        }))
        .filter(r => Number.isFinite(r.t) && Number.isFinite(r.o) && Number.isFinite(r.h)
                  && Number.isFinite(r.l) && Number.isFinite(r.c))
        .sort((a,b) => a.t - b.t);
      this.historyBars[range] = bars;
      this.render();
    }catch(e){
      // Network hiccup — not fatal, that range just falls back to
      // tick-bucketing (today's behavior) until a retry succeeds.
      console.warn('[priceChart] hydrateRange error:', e);
    }finally{
      this._hydratingRanges.delete(range);
    }
  }

  // One-time backfill from the backend's short-term tick history, so a
  // page load/reload doesn't have to sit and wait for the widest range
  // (ALL) to fill in tick-by-tick. Call once, before the WS connection
  // starts feeding addTick() — bails out quietly (chart just starts empty
  // and builds up live, same as today) if the endpoint isn't there yet or
  // errors, so this is safe to ship ahead of the backend work.
  async hydrate(url){
    if(this._hydrateStarted) return; // only ever hydrate once, not "only if ticks are empty"
    this._hydrateStarted = true;
    try{
      const res = await fetch(url);
      if(!res.ok){ console.warn('[priceChart] hydrate failed:', res.status, res.statusText, url); return; }
      const rows = await res.json(); // expected: [{t: epoch ms, p: price}, ...] oldest→newest
      if(!Array.isArray(rows) || !rows.length) return;
      const hydrated = rows
        .map(r => ({ t: Number(r.t), p: parseFloat(r.p), vw: (r.vw != null && isFinite(r.vw)) ? Number(r.vw) : null }))
        .filter(r => Number.isFinite(r.t) && Number.isFinite(r.p))
        .sort((a,b) => a.t - b.t);
      // If a live tick snuck in before or during this fetch (very common —
      // the WS connection routinely beats this fetch to the punch), keep
      // it — splice the backfill in front rather than overwriting.
      this.ticks = hydrated.concat(this.ticks);
      if(this.ticks.length > this.MAX_TICKS) this.ticks = this.ticks.slice(-this.MAX_TICKS);
      this.render();
    }catch(e){
      // Network hiccup or endpoint not deployed yet — not fatal, chart
      // just behaves exactly as it does today (builds up from mount).
      console.warn('[priceChart] hydrate error:', e);
    }
  }

  _visibleTicks(){
    const cfg = PRICE_CHART_RANGES[this.settings.range] || PRICE_CHART_RANGES['5m'];
    if(cfg.ms === Infinity) return this.ticks;
    const cutoff = Date.now() - cfg.ms;
    const idx = this.ticks.findIndex(tk => tk.t >= cutoff);
    return idx < 0 ? [] : this.ticks.slice(idx);
  }

  _aggregateCandles(ticks, bucketMs){
    if(!ticks.length) return [];
    const candles = [];
    let cur = null;
    for(const tk of ticks){
      const bucketStart = Math.floor(tk.t / bucketMs) * bucketMs;
      if(!cur || cur.t !== bucketStart){
        if(cur) candles.push(cur);
        cur = { t: bucketStart, o: tk.p, h: tk.p, l: tk.p, c: tk.p, vw: tk.vw };
      } else {
        cur.h = Math.max(cur.h, tk.p);
        cur.l = Math.min(cur.l, tk.p);
        cur.c = tk.p;
        if(tk.vw != null) cur.vw = tk.vw;
      }
    }
    if(cur) candles.push(cur);
    return candles;
  }

  _sma(values, period){
    const out = new Array(values.length).fill(null);
    if(period <= 1 || values.length < period) return out;
    let sum = 0;
    for(let i = 0; i < values.length; i++){
      sum += values[i];
      if(i >= period) sum -= values[i - period];
      if(i >= period - 1) out[i] = sum / period;
    }
    return out;
  }

  _ema(values, period){
    const out = new Array(values.length).fill(null);
    if(period <= 1 || values.length < period) return out;
    const k = 2 / (period + 1);
    let prev = values.slice(0, period).reduce((a,b)=>a+b,0) / period;
    out[period - 1] = prev;
    for(let i = period; i < values.length; i++){
      prev = values[i] * k + prev * (1 - k);
      out[i] = prev;
    }
    return out;
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
    return `
      <div class="pc-panel" style="background:var(--card-bg,#1E2028);border-radius:10px;padding:12px;margin-bottom:16px;">
        <div class="pc-toolbar">
          <span class="pc-title">Live Price</span>
          <span class="pc-spot-readout" id="pc-spot-readout"></span>
          <span class="pc-spacer"></span>
          <label class="pc-field">
            Period
            <select id="pc-range-select">${['1m','5m','15m','1h','1d','all'].map(rangeOpt).join('')}</select>
          </label>
          <label class="pc-field">
            Field
            <select id="pc-field-select">${fieldOpt('o','Open')}${fieldOpt('h','High')}${fieldOpt('l','Low')}${fieldOpt('c','Close')}</select>
          </label>
          ${typeBtn('line','Line')}
          ${typeBtn('candle','Candles')}
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
            <input type="checkbox" id="pc-grid-toggle" ${s.showGrid?'checked':''}/> Grid
          </label>
          <label class="pc-field">
            Color <input type="color" id="pc-line-color" value="${s.lineColor}"/>
          </label>
        </div>
        <div class="pc-chart-wrap">
          <canvas id="price-chart-canvas" style="width:100%;display:block;" height="220"></canvas>
          ${this._orderPanelHtml()}
        </div>
        <div class="pc-footnote" id="pc-vwap-footnote">VWAP unavailable — this symbol's feed doesn't carry a volume field yet.</div>
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

    // ── Zoom (wheel) / pan (drag) / reset (double-click) ──
    // Uses this._lastRenderCtx (set at the end of render()) to convert
    // mouse pixel positions into chart time coordinates — avoids
    // recomputing the window/series logic outside render().
    const canvas = document.getElementById('price-chart-canvas');
    if(canvas && !canvas._pcZoomWired){
      canvas._pcZoomWired = true;
      canvas.style.cursor = 'grab';

      canvas.onwheel = (e) => {
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
      };

      let drag = null;
      canvas.onmousedown = (e) => {
        const rc = this._lastRenderCtx;
        if(!rc) return;
        drag = {
          startX: e.clientX,
          winStart: this._zoomStart != null ? this._zoomStart : rc.windowStart,
          winEnd: this._zoomEnd != null ? this._zoomEnd : rc.windowStart + rc.span,
        };
        canvas.style.cursor = 'grabbing';
      };
      window.addEventListener('mousemove', (e) => {
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
        if(drag){ drag = null; canvas.style.cursor = 'grab'; }
      });
      canvas.ondblclick = () => { this._zoomStart = null; this._zoomEnd = null; this.render(); };
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
    if(!points.length){
      return { toVirtual: t => t, toReal: v => v, virtualStart: 0, virtualEnd: 0 };
    }
    const segs = [{ real: points[0].t, virtual: 0, gapBefore: false }];
    let vCursor = 0;
    for(let i = 1; i < points.length; i++){
      const delta = points[i].t - points[i - 1].t;
      const isGap = delta > gapThreshold;
      vCursor += isGap ? gapCap : delta;
      segs.push({ real: points[i].t, virtual: vCursor, gapBefore: isGap });
    }
    const first = segs[0], last = segs[segs.length - 1];

    const findSeg = (arr, key, val) => {
      let lo = 0, hi = arr.length - 1;
      while(hi - lo > 1){
        const mid = (lo + hi) >> 1;
        if(arr[mid][key] <= val) lo = mid; else hi = mid;
      }
      return [arr[lo], arr[hi]];
    };

    const toVirtual = (t) => {
      if(t <= first.real) return first.virtual - (first.real - t);
      if(t >= last.real) return last.virtual + (t - last.real);
      const [a, b] = findSeg(segs, 'real', t);
      const realDelta = b.real - a.real;
      if(realDelta <= 0) return a.virtual;
      return a.virtual + ((t - a.real) / realDelta) * (b.virtual - a.virtual);
    };
    const toReal = (v) => {
      if(v <= first.virtual) return first.real - (first.virtual - v);
      if(v >= last.virtual) return last.real + (v - last.virtual);
      const [a, b] = findSeg(segs, 'virtual', v);
      const virtDelta = b.virtual - a.virtual;
      if(virtDelta <= 0) return a.real;
      return a.real + ((v - a.virtual) / virtDelta) * (b.real - a.real);
    };
    // True when t falls strictly inside a compressed (non-trading) gap —
    // i.e. between two consecutive points whose real spacing exceeded
    // gapThreshold. Used to drop axis labels that would otherwise print a
    // clock time (e.g. "7:00") for a stretch with no actual candles.
    const isInGap = (t) => {
      if(t <= first.real || t >= last.real) return false;
      const [, b] = findSeg(segs, 'real', t);
      return b.gapBefore === true;
    };
    return { toVirtual, toReal, isInGap, virtualStart: first.virtual, virtualEnd: last.virtual };
  }

  _refreshToolbarState(){
    const panel = document.getElementById('price-chart-section');
    if(!panel) return;
    panel.querySelectorAll('.pc-type-btn').forEach(b=>b.classList.toggle('pc-active', b.dataset.type===this.settings.type));
  }

  // ── RENDER ───────────────────────────────────────────
  render(forceResize){
    this.ensureMounted();
    const canvas = document.getElementById('price-chart-canvas');
    if(!canvas) return;

    const readout = $i('pc-spot-readout');
    const lastTick = this.ticks[this.ticks.length - 1];
    if(readout) readout.textContent = lastTick ? fmtI(lastTick.p) : '—';
    this._syncOrderPanel(lastTick);

    const visible = this._visibleTicks();
    if(!visible.length){
      const W0 = canvas.parentElement.clientWidth - 24, H0 = 220;
      const ctx = sizeCanvasIfChanged(canvas, W0, H0);
      ctx.clearRect(0,0,W0,H0);
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

    const W0 = canvas.parentElement.clientWidth - 24, H0 = 220;
    const ctx = sizeCanvasIfChanged(canvas, W0, H0);
    const W = W0, H = H0;
    const PAD = { l: 54, r: 12, t: 12, b: 22 };
    const PW = W - PAD.l - PAD.r, PH = H - PAD.t - PAD.b;

    ctx.clearRect(0,0,W,H);

    let series, values;
    const bars = this.historyBars[this.settings.range];
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
      const lastBarT = bars[bars.length - 1].t;
      const liveTail = visible.filter(tk => tk.t > lastBarT);
      const tailCandles = liveTail.length ? this._aggregateCandles(liveTail, cfg.bucketMs) : [];
      let merged = bars.concat(tailCandles).filter(c => c.t >= windowStart && c.t <= windowEnd);
      if(this.settings.type === 'candle'){
        series = merged;
      } else {
        // Line mode reads t.p per point — alias the selected OHLC field
        // (open/high/low/close) onto .p rather than rewriting the
        // line-draw path below.
        series = merged.map(c => ({ t: c.t, p: c[_fieldKey], vw: null }));
      }
      values = merged.map(c=>c[_fieldKey]);
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

    const yMin = Math.min(...(this.settings.type==='candle'?series.map(c=>c.l):values));
    const yMax = Math.max(...(this.settings.type==='candle'?series.map(c=>c.h):values));
    const padY = (yMax - yMin) * 0.1 || 1;
    const y0 = yMin - padY, y1 = yMax + padY;

    // Fully compress non-trading gaps (overnight/weekend) to zero width —
    // consecutive sessions sit directly back-to-back, same as any real
    // trading platform's chart. Anything bigger than ~4 candle-widths
    // (min 20 min) counts as "not trading hours" and gets collapsed.
    const GAP_THRESHOLD = Math.max(cfg.bucketMs * 4, 20 * 60 * 1000);
    const GAP_CAP = 0;
    // Treat the window's own start/end as points in the same map as the
    // candles — this is what makes pre-market dead space (windowStart up
    // to the first real candle, e.g. a 6.25hr lookback window starting
    // before 09:15 open) collapse exactly like an overnight gap does,
    // instead of only compressing gaps *between* candles and leaving the
    // leading/trailing edges as raw uncompressed real time.
    const mapPoints = series.slice();
    if(mapPoints.length && windowStart < mapPoints[0].t) mapPoints.unshift({ t: windowStart });
    if(mapPoints.length && windowEnd > mapPoints[mapPoints.length - 1].t) mapPoints.push({ t: windowEnd });
    const tmap = this._buildTimeMap(mapPoints, GAP_THRESHOLD, GAP_CAP);
    const vWindowStart = tmap.toVirtual(windowStart);
    const vWindowEnd = tmap.toVirtual(windowEnd);
    const vSpan = Math.max(1, vWindowEnd - vWindowStart);

    const xScale = t => PAD.l + ((tmap.toVirtual(t) - vWindowStart) / vSpan) * PW;
    const yScale = v => PAD.t + PH - ((v - y0)/(y1 - y0)) * PH;

    // Expose the pixel<->time mapping this render used, so wheel/drag
    // handlers (which run outside render()) can convert mouse coordinates
    // without duplicating this logic.
    this._lastRenderCtx = { windowStart, windowEnd, span, PAD, PW, dataMinT, dataMaxT, minSpan, tmap, vWindowStart, vSpan };

    if(this.settings.showGrid){
      ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
      for(let g=0; g<=4; g++){
        const gy = PAD.t + (g/4)*PH;
        ctx.beginPath(); ctx.moveTo(PAD.l, gy); ctx.lineTo(W-PAD.r, gy); ctx.stroke();
        const val = y1 - (g/4)*(y1-y0);
        ctx.fillStyle = C.axisLbl; ctx.font = '10px Inter,sans-serif'; ctx.textAlign='right';
        ctx.fillText(fmtI(val), PAD.l-6, gy+3);
      }
    }

    // ── X-AXIS TIME LABELS ──
    // Snapped to round clock boundaries appropriate to the selected range
    // (whole minutes for 5M, whole seconds only for 1M, etc.) instead of
    // 6 raw fractions of the window — the old approach recomputed label
    // timestamps as a fraction of "now" every frame, so on a live rolling
    // window the seconds digits visibly ticked every single render even on
    // the 5M/15M views, which read as "it's still on a 1-second timeframe".
    const LABEL_STEP_MS = {
      '1m': 10 * 60 * 1000,      // 1hr window   → every 10 min (~6 labels)
      '5m': 60 * 60 * 1000,      // 6.25hr window → every 1 hr  (~6 labels)
      '15m': 60 * 60 * 1000,     // 6.25hr window → every 1 hr  (~6 labels)
      '1h': 6 * 60 * 60 * 1000,  // 32hr window   → every 6 hr  (~5 labels)
      '1d': 14 * 24 * 60 * 60 * 1000, // 90d window → every 14 days (~6 labels)
    };
    let labelStep = LABEL_STEP_MS[this.settings.range];
    if(!labelStep){ // 'all' — no fixed range, pick a step that yields ~6 labels
      const niceSteps = [1000,5000,10000,30000,60000,300000,600000,900000,1800000,3600000,7200000,14400000,21600000,43200000,86400000,7*86400000,14*86400000,30*86400000,60*86400000,90*86400000,180*86400000,365*86400000,730*86400000,1825*86400000];
      labelStep = niceSteps.find(s => s >= span/6) || niceSteps[niceSteps.length-1];
    }
    const showSeconds = labelStep < 60000;
    const showDate = labelStep >= 24 * 60 * 60 * 1000; // 1D range, or 'all' zoomed out past a day
    const fmtOpts = showDate
      ? { month:'short', day:'numeric' }
      : showSeconds
        ? { hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false }
        : { hour:'2-digit', minute:'2-digit', hour12:false };
    ctx.font = '10px Inter,sans-serif'; ctx.textAlign = 'center';
    for(let t = Math.ceil(windowStart/labelStep)*labelStep; t <= windowEnd; t += labelStep){
      if(tmap.isInGap(t)) continue;
      const x = xScale(t);
      if(x < PAD.l - 1 || x > W - PAD.r + 1) continue;
      if(this.settings.showGrid){
        ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x, PAD.t); ctx.lineTo(x, PAD.t+PH); ctx.stroke();
      }
      const label = showDate ? new Date(t).toLocaleDateString([], fmtOpts) : new Date(t).toLocaleTimeString([], fmtOpts);
      ctx.fillStyle = C.axisLbl;
      ctx.fillText(label, x, H - 6);
    }
    ctx.textAlign = 'left';

    if(this.settings.type === 'candle'){
      // Candle width is derived from the bucket period against the
      // (possibly gap-compressed) virtual span, not real span — within a
      // session virtual delta == real delta == bucketMs, so this stays
      // accurate whether or not the current window crosses a compressed
      // overnight/weekend gap.
      const bucketPx = cfg.bucketMs / vSpan * PW;
      const cw = Math.max(2, bucketPx * 0.6);
      series.forEach((c)=>{
        const x = xScale(c.t);
        const up = c.c >= c.o;
        ctx.strokeStyle = ctx.fillStyle = up ? C.up : C.down;
        ctx.beginPath(); ctx.moveTo(x, yScale(c.h)); ctx.lineTo(x, yScale(c.l)); ctx.stroke();
        const bodyTop = yScale(Math.max(c.o,c.c)), bodyBot = yScale(Math.min(c.o,c.c));
        ctx.fillRect(x-cw/2, bodyTop, cw, Math.max(1, bodyBot-bodyTop));
      });
    } else {
      ctx.strokeStyle = C.line; ctx.lineWidth = 1.6;
      if(this.settings.glow){ ctx.shadowColor = C.lineGlow; ctx.shadowBlur = 6; }
      ctx.beginPath();
      series.forEach((t,i)=>{ const x=xScale(t.t), y=yScale(t.p); i===0?ctx.moveTo(x,y):ctx.lineTo(x,y); });
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    const drawOverlay = (periods, color, fn) => {
      periods.forEach(p=>{
        const out = fn(values, p);
        ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.beginPath();
        let started = false;
        out.forEach((v,i)=>{
          if(v==null) return;
          const x = xScale(series[i].t), y = yScale(v);
          if(!started){ ctx.moveTo(x,y); started = true; } else ctx.lineTo(x,y);
        });
        ctx.stroke();
      });
    };
    if(this.settings.smaPeriods.length) drawOverlay(this.settings.smaPeriods, C.sma, this._sma.bind(this));
    if(this.settings.emaPeriods.length) drawOverlay(this.settings.emaPeriods, C.ema, this._ema.bind(this));

    // VWAP isn't a windowed function of `values` like SMA/EMA — each point
    // already IS the session VWAP as of that tick (Value/Volume from
    // allIndices), so just plot series[i].vw directly, dashed to
    // distinguish it from the SMA/EMA overlays at a glance.
    let lastVwap = null;
    if(this.settings.showVwap){
      ctx.save();
      ctx.strokeStyle = C.vwap; ctx.lineWidth = 1.2; ctx.setLineDash([4,3]);
      ctx.beginPath();
      let started = false;
      series.forEach((pt)=>{
        if(pt.vw == null) return;
        lastVwap = pt.vw;
        const x = xScale(pt.t), y = yScale(pt.vw);
        if(!started){ ctx.moveTo(x,y); started = true; } else ctx.lineTo(x,y);
      });
      ctx.stroke();
      ctx.restore();
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
