// ============================================================
// fiidii-report.js
// Flow chart / long-short ratio gauge / participant OI bars / sector
// heatmap — folded in from the old standalone FII-DII.html terminal page
// into the #fiidii-dashboard-modal in DashboardPro.html, as extra
// sections below the existing participant-OI table (ExecView.
// buildFiiDiiCard, still rendered into #fiidii-modal-content exactly as
// before, untouched).
//
// FII-DII.html's IV skew panel was dropped rather than carried over —
// DashboardPro already has a separate, more accurate IV Surface modal
// built from live chain data (ChainView.renderIvSurfaceModal), and the
// two "IV skew" panels would otherwise disagree since this endpoint's
// skew curve is coarser. See openIvSurfaceModal() for that one.
//
// Rendering logic (blockBar/drawFlow/renderSectors/etc.) is carried over
// near-verbatim from FII-DII.html, just:
//   - element ids prefixed "fd" (fdFlowCanvas, fdOiRows, ...) so they
//     can't collide with any other id on this page
//   - colors pulled from theme.css vars (--green/--red/--amber/--txt3/
//     --fd-violet/--fd-grey — see fiidii-report.css) instead of
//     FII-DII.html's own hardcoded hex palette
//   - class names prefixed "fd-" (fd-panel, fd-oi-row, ...) instead of
//     the old page's bare .panel/.oi-row/etc.
//
// DATA SOURCE: this is genuinely separate data from the rest of the
// dashboard's tick (d.fiiDiiSentiment feeds the table above; this feeds
// off ws_server_live.py's /dashboard-relay endpoint — flow/ratio/oi/
// sectors (skew ignored — see above), built from nse_fii_dii_flow_fetch.py
// + fii_dii_sentiment.py + market_api.py on its own 2s loop, same endpoint
// FII-DII.html used to connect to directly). Rather than keep that as a
// permanent second WebSocket open for the lifetime of the page,
// FiiDiiReportFeed connects only while the FII/DII modal is open (see
// modal-manager.js's openFiiDiiModal/closeFiiDiiModal) and disconnects on
// close.
// ============================================================

// ============ STATE ============
let fdFlowSeries = { fii: new Array(30).fill(0), dii: new Array(30).fill(0) };
let fdOiData = [
  { name: 'FII',    pct: 0, colorVar: '--fd-violet', trend: '--', dir: 'flat' },
  { name: 'PRO',    pct: 0, colorVar: '--amber',      trend: '--', dir: 'flat' },
  { name: 'RETAIL', pct: 0, colorVar: '--fd-grey',    trend: '--', dir: 'flat' },
  { name: 'DII',    pct: 0, colorVar: '--green',      trend: '--', dir: 'flat' },
];
let fdSectors = [];
let _fdLiveDataReceived = false;

// Resolve a CSS custom prop (e.g. '--fd-violet') to its computed value —
// same approach FII-DII.html used for canvas fill colors (canvas can't
// read var() directly).
function _fdCssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#7c8798';
}

// ============ HELPER: terminal-style block bar ============
function fdBlockBar(pct, blocks = 20, color = null, showPct = true) {
  const filled = Math.max(0, Math.min(blocks, Math.round((pct / 100) * blocks)));
  const empty = blocks - filled;
  const fillColor = color ? `style="color:${color}"` : '';
  const label = showPct ? ` ${pct.toFixed(1)}%` : '';
  return `[<span class="fd-fill" ${fillColor}>${'▓'.repeat(filled)}</span><span class="fd-void">${'░'.repeat(empty)}</span>]${label}`;
}

// ============ RENDER: long-short ratio (ASCII bar + headline stats) ============
function fdBiasLabelFromPct(pct) {
  if (pct >= 55) return 'STRONG BULLISH BIAS';
  if (pct >= 50) return 'MODERATE BULLISH BIAS';
  if (pct >= 45) return 'NEUTRAL';
  if (pct >= 35) return 'MODERATE BEARISH BIAS';
  return 'STRONG BEARISH BIAS';
}

// Accepts either a plain number (the real /dashboard-relay shape — bar +
// derived bias only) or {pct, bias, avg20d, avgTrend} for a richer shape.
function fdRenderRatioBar(input) {
  const isObj = input && typeof input === 'object';
  const pct = isObj ? input.pct : input;
  if (typeof pct !== 'number' || Number.isNaN(pct)) return;

  const barEl = document.getElementById('fdRatioBar');
  if (barEl) barEl.innerHTML = fdBlockBar(pct, 22, null, false);

  const pctEl = document.getElementById('fdGaugePct');
  if (pctEl) pctEl.textContent = pct.toFixed(1) + '%';

  const biasEl = document.getElementById('fdGaugeBias');
  if (biasEl) biasEl.textContent = isObj && input.bias ? input.bias : fdBiasLabelFromPct(pct);

  const subEl = document.getElementById('fdGaugeSub');
  if (subEl) {
    if (isObj && typeof input.avg20d === 'number') {
      const trend = input.avgTrend || (pct < input.avg20d ? 'trending lower' : pct > input.avg20d ? 'trending higher' : 'flat vs avg');
      subEl.textContent = `20D avg ${input.avg20d.toFixed(1)}% · long share of FII futures OI (current level), ${trend}`;
    } else if (!isObj) {
      subEl.textContent = 'long share of FII futures OI (current level, not day-over-day change)';
    }
  }
}

// ============ RENDER: OI breakdown (ASCII bars) ============
function fdRenderOI(data) {
  const el = document.getElementById('fdOiRows');
  if (!el) return;
  el.innerHTML = data.map(o => `
    <div class="fd-oi-row">
      <span class="fd-oi-name">${o.name}</span>
      <span class="fd-ascii-bar fd-small">${fdBlockBar(o.pct, 16, _fdCssVar(o.colorVar))}</span>
      <span class="fd-oi-trend fd-${o.dir}">${o.trend}</span>
    </div>
  `).join('');
}

// ============ RENDER: sector heatmap ============
function fdRenderSectors(data) {
  const el = document.getElementById('fdHeatGrid');
  if (!el) return;
  el.innerHTML = data.map(s => `
    <div class="fd-sector">
      <div class="fd-sector-head">
        <span>${s.name}</span>
        <span class="fd-sector-tag ${s.cls}">${s.tag}</span>
      </div>
      <div class="fd-sector-body">
        ${s.stocks.map(st => `
          <div class="fd-stock">
            <span class="fd-stock-name">${st.n}</span>
            <span class="fd-stock-chg fd-${st.dir}">${st.v}</span>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');
}

// ============ RENDER: flow legend (right-side totals) ============
function fdFmtCr(n) {
  const sign = n >= 0 ? '+' : '-';
  return sign + Math.abs(Math.round(n)).toLocaleString('en-IN');
}
function fdRenderFlowLegend() {
  const fiiLatest = fdFlowSeries.fii[fdFlowSeries.fii.length - 1] || 0;
  const diiLatest = fdFlowSeries.dii[fdFlowSeries.dii.length - 1] || 0;
  const net = fiiLatest + diiLatest;

  const fiiEl = document.getElementById('fdLegendFii');
  const diiEl = document.getElementById('fdLegendDii');
  const netEl = document.getElementById('fdLegendNet');
  if (!fiiEl || !diiEl || !netEl) return;

  fiiEl.textContent = fdFmtCr(fiiLatest);
  fiiEl.className = 'fd-legend-val ' + (fiiLatest >= 0 ? 'fd-up' : 'fd-down');
  diiEl.textContent = fdFmtCr(diiLatest);
  diiEl.className = 'fd-legend-val ' + (diiLatest >= 0 ? 'fd-up' : 'fd-down');
  netEl.textContent = fdFmtCr(net);
  netEl.className = 'fd-legend-val ' + (net >= 0 ? 'fd-up' : 'fd-down');
}

// ============ CHART: FII/DII flow (canvas bars) ============
function fdDrawFlow() {
  const canvas = document.getElementById('fdFlowCanvas');
  if (!canvas) return;
  const parent = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const W = Math.round(parent.clientWidth);
  const H = Math.round(parent.clientHeight);
  if (!W || !H) return;

  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';
  canvas.width = W * dpr;
  canvas.height = H * dpr;

  const ctx = canvas.getContext('2d');
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  const n = fdFlowSeries.fii.length;
  const all = [...fdFlowSeries.fii, ...fdFlowSeries.dii];
  const max = Math.max(...all), min = Math.min(...all);
  const range = Math.max(Math.abs(max), Math.abs(min)) || 1;
  const zeroY = H / 2;
  const barW = (W / n) * 0.34;
  const gap = W / n;

  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, zeroY); ctx.lineTo(W, zeroY); ctx.stroke();

  const violet = _fdCssVar('--fd-violet');
  const grey = _fdCssVar('--fd-grey');

  for (let i = 0; i < n; i++) {
    const x = i * gap + gap * 0.16;
    const fiiVal = fdFlowSeries.fii[i];
    const diiVal = fdFlowSeries.dii[i];
    const fiiH = (Math.abs(fiiVal) / range) * (H / 2 - 6);
    const diiH = (Math.abs(diiVal) / range) * (H / 2 - 6);

    ctx.fillStyle = violet;
    if (fiiVal >= 0) ctx.fillRect(x, zeroY - fiiH, barW, fiiH);
    else ctx.fillRect(x, zeroY, barW, fiiH);

    ctx.fillStyle = grey;
    const x2 = x + barW + 2;
    if (diiVal >= 0) ctx.fillRect(x2, zeroY - diiH, barW, diiH);
    else ctx.fillRect(x2, zeroY, barW, diiH);
  }
}

function fdMarkLive() {
  if (_fdLiveDataReceived) return;
  _fdLiveDataReceived = true;
  const statusEl = document.getElementById('fdReportStatus');
  if (statusEl) { statusEl.textContent = 'LIVE — ' + new Date().toDateString(); statusEl.classList.add('live'); }
}

// Same shape ws_server_live.py's bridge_ws_handler()/bridge_loop()
// snapshot has always sent — see FII-DII.html's former window.updateDashboard
// doc comment for the full field-by-field shape. quotes intentionally
// ignored — this report has no ticker strip.
function fdUpdateReport(payload = {}) {
  // payload.bias (analytics/fii_dii_market_bias.py) no longer renders here —
  // that card was a duplicate of the one already shown on the main
  // dashboard (ExecView.buildFiiDiiSummaryCard, explicitly qualified as
  // combined cash+F&O context beneath the cash-flow summary). The modal
  // therefore does not repeat the bias card. Still counts toward
  // "is this feed live" below.
  if (payload.bias) { fdMarkLive(); }
  if (payload.flow) { fdFlowSeries = payload.flow; fdDrawFlow(); fdRenderFlowLegend(); fdMarkLive(); }
  if (typeof payload.ratio === 'number' || (payload.ratio && typeof payload.ratio === 'object')) {
    fdRenderRatioBar(payload.ratio);
    fdMarkLive();
  }
  if (payload.oi) {
    // Backend sends {name,pct,color,trend,dir} — color is dropped in favor
    // of this file's own colorVar-per-participant list above, since the
    // old hex-string shape doesn't map onto theme.css vars.
    fdOiData = fdOiData.map((o, i) => payload.oi[i] ? { ...o, pct: payload.oi[i].pct, trend: payload.oi[i].trend, dir: payload.oi[i].dir } : o);
    fdRenderOI(fdOiData);
    fdMarkLive();
  }
  if (payload.sectors) { fdSectors = payload.sectors; fdRenderSectors(fdSectors); fdMarkLive(); }
}

window.addEventListener('resize', () => { if (_fdModalIsOpen()) requestAnimationFrame(fdDrawFlow); });
function _fdModalIsOpen() {
  const modal = document.getElementById('fiidii-dashboard-modal');
  return !!(modal && modal.classList.contains('open'));
}

// ==========================================================================
// LIVE CONNECTION — connects only while the FII/DII modal is open (see
// modal-manager.js's openFiiDiiModal/closeFiiDiiModal). ws_server_live.py's
// /dashboard-relay is a real, self-contained endpoint independent of the
// main /ws feed — it does its own NSE/EOD fetches (fii_dii_sentiment.py,
// nse_fii_dii_flow_fetch.py, market_api.py) on a 2s loop and sends a full
// {quotes, skew, sectors, ratio, oi, flow} snapshot on connect, then live
// pushes after. Reuses Config.ws (shared/config.js) for the same-origin
// host/scheme handling price-chart-standalone.js/data-service.js already
// rely on, rather than FII-DII.html's old hardcoded `:5500`.
// ==========================================================================
const FiiDiiReportFeed = (() => {
  let sock = null;
  let retryTimer = null;

  function relayUrl() {
    if (window.Config && Config.ws && Config.ws.relayUrl) return Config.ws.relayUrl;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/dashboard-relay`;
  }

  function setStatus(text, live) {
    const el = document.getElementById('fdReportStatus');
    if (!el) return;
    el.textContent = text;
    el.classList.toggle('live', !!live);
  }

  function connect() {
    if (sock) return; // already connected
    _fdLiveDataReceived = false;
    setStatus('CONNECTING…', false);
    sock = new WebSocket(relayUrl());
    sock.onopen = () => Logger.info('fiidii-report', 'connected to /dashboard-relay');
    sock.onmessage = (evt) => {
      try { fdUpdateReport(JSON.parse(evt.data)); }
      catch (e) { Logger.error('fiidii-report', 'bad payload', e); }
    };
    sock.onclose = () => {
      sock = null;
      if (!_fdModalIsOpen()) return; // closed deliberately by disconnect()
      if (!_fdLiveDataReceived) setStatus('RETRYING…', false);
      retryTimer = setTimeout(connect, 5000);
    };
    sock.onerror = () => { if (sock) sock.close(); };
  }

  function disconnect() {
    clearTimeout(retryTimer);
    if (sock) { sock.onclose = null; sock.close(); sock = null; }
  }

  return { connect, disconnect };
})();
if (typeof window !== 'undefined') window.FiiDiiReportFeed = FiiDiiReportFeed;

// Initial idle paint so the panels aren't blank the instant the modal
// markup exists, before the first connect() ever fires.
document.addEventListener('DOMContentLoaded', () => {
  fdRenderOI(fdOiData);
  fdRenderSectors(fdSectors);
});