// ═══════════════════════════════════════════════════════════════════
// OI DASHBOARD (oi_dashboard.html) — extracted from that file's inline
// <script> block on 2026-07-12; restructured to a 3-level tab hierarchy
// on 2026-07-18 (top-level Bar Chart/Butterfly/GEX in #tabs, and a single
// merged mode row in #modeTabs: 5/15/30/Intraday window buttons on the
// left, OI Chg/OI/Combined mode buttons on the right, all one line).
// WebSocket connect/reconnect, parent postMessage feed, mock-data
// fallback, and the canvas chart renderers (grouped bars, striped
// combined view) are otherwise unchanged. See oi_dashboard.html for the
// markup this binds to (#oiCanvas, #tabs, #modeTabs, #legendRow,
// #connBadge, #wsUrlInput, #bflyWrap, etc).
// ═══════════════════════════════════════════════════════════════════

// ─────────────────────────────────────────────────────────────
// COLORS (real values — canvas can't read CSS var(), so hardcode)
// ─────────────────────────────────────────────────────────────
const C = {
  bg0:'#0B0E14', border:'rgba(255,255,255,0.08)', txt:'#E8EDF2', txt2:'#8A94A6', txt3:'#5E6B7E',
  green:'#3FAE5A', red:'#C0392B', blue:'#2F6FD6', amber:'#FCC419'
};

// ─────────────────────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────────────────────
// ── TAB STATE (three-level hierarchy) ──
// Top level:    currentView  = 'bar' | 'butterfly' | 'gex'
// Mode (shared by bar & butterfly, hidden for gex): currentMode = 'oi' | 'chg' | 'combined'
// Window (only relevant when currentMode === 'chg'): chgWindow = '5' | '15' | '30' | 'all'
//   'all' (shown as "Intraday" in the UI) = plain total OI change since
//   day-open anchor (the old flat "OI Chng" figure).
//   '5'/'15'/'30' = rolling velocity window from OI_VELOCITY.
let currentView = 'bar';
let currentMode = 'oi';
let chgWindow = 'all';

let SPOT = null, ATM_STRIKE = null;
let CHAIN = [];          // [{strike, peOI, ceOI, peOIChg, ceOIChg, peOIplus, peOIminus, ceOIplus, ceOIminus, gex}]
let OI_VELOCITY = [];    // [{window: 5|15|30, rows: [{strike, ceDOI, peDOI}, ...]}] — same shape chain-views.js reads d.oiVelocity as
let ws = null;
let usingMock = false;
let mockTimer = null;

// ─────────────────────────────────────────────────────────────
// PARENT PUSH CHANNEL 
// ─────────────────────────────────────────────────────────────
window.addEventListener('message', function (e) {
  if (!e.data || e.data.type !== 'OI_DASHBOARD_DATA') return;
  if (adaptLiveMessage(e.data.payload)) {
    clearInterval(mockTimer);
    usingMock = false;
    setBadge('live', 'live · parent feed');
    document.getElementById('lastMsg').textContent =
      'updated ' + new Date().toLocaleTimeString('en-IN', { hour12: false });
    render();
  }
});
// ─────────────────────────────────────────────────────────────
// DELTA MERGE HELPER
// Merges SmartAPI's partial "changed" rows into the existing CHAIN
// by strike, instead of expecting a full flat array every time.
// Only overwrites fields the delta actually carries (partial ceLTP/ceOI
// ticks shouldn't blank out peOI etc) — mirrors the None-guard already
// used on the Python side in TickAggregator.on_tick().
// ─────────────────────────────────────────────────────────────
function mergeDeltaIntoChain(changedRows) {
  if (!Array.isArray(changedRows) || changedRows.length === 0) return false;
  if (SPOT == null || isNaN(SPOT)) {
    // No full snapshot has established SPOT yet — merging now would
    // corrupt ATM_STRIKE math downstream. Wait for the next full payload.
    console.warn('[oi-dashboard] delta arrived before SPOT was known, skipping merge.');
    return false;
  }
  if (!Array.isArray(CHAIN) || CHAIN.length === 0) {
    // No base chain to merge into yet either — same reasoning.
    console.warn('[oi-dashboard] delta arrived before CHAIN was seeded, skipping merge.');
    return false;
  }

  const byStrike = new Map(CHAIN.map(r => [r.strike, r]));
  for (const raw of changedRows) {
    const strike = Number(raw.strike);
    if (isNaN(strike)) continue;
    const existing = byStrike.get(strike);
    if (!existing) continue; // unknown strike (different expiry/session) — ignore rather than fabricate a partial row

    if (raw.ceOI !== undefined) existing.ceOI = Number(raw.ceOI ?? 0);
    if (raw.peOI !== undefined) existing.peOI = Number(raw.peOI ?? 0);
    if (raw.ceChgOI !== undefined) {
      existing.ceOIChg = Number(raw.ceChgOI ?? 0);
      existing.ceOIplus = Math.max(0, existing.ceOIChg);
      existing.ceOIminus = Math.max(0, -existing.ceOIChg);
    }
    if (raw.peChgOI !== undefined) {
      existing.peOIChg = Number(raw.peChgOI ?? 0);
      existing.peOIplus = Math.max(0, existing.peOIChg);
      existing.peOIminus = Math.max(0, -existing.peOIChg);
    }
  }
  CHAIN = Array.from(byStrike.values()).sort((a, b) => a.strike - b.strike);
  return true;
}

// ─────────────────────────────────────────────────────────────
// LIVE DATA ADAPTER
// ─────────────────────────────────────────────────────────────
function adaptLiveMessage(msg) {
  if (!msg || typeof msg !== 'object') return false;

  // SmartAPI delta ticks: {"type":"delta","payload":{"chain":{"_keyed":true,"changed":[...]}}}
  // OBSERVE-FIRST: log the shape and bail without touching CHAIN, so you
  // can confirm the payload matches expectations in the console before
  // this starts actually mutating chart state.
  if (msg.type === 'delta') {
    if (!window.__deltaShapeLogged) {
      window.__deltaShapeLogged = true;
      console.log('[oi-dashboard] delta received (shape logged once):', JSON.stringify(msg));
    }
    return false;
    // Once confirmed correct, replace the two lines above with:
    //
    // const chainDelta = msg.payload && msg.payload.chain;
    // if (chainDelta && chainDelta._keyed && Array.isArray(chainDelta.changed)) {
    //   return mergeDeltaIntoChain(chainDelta.changed);
    // }
    // return false;
  }

  const body = (msg.type === 'full' && msg.payload) ? msg.payload : msg;
  const strikesArr = body.chain;
  if (!Array.isArray(strikesArr)) {
    console.warn('[oi-dashboard] connected, but payload.chain missing.', Object.keys(body));
    document.getElementById('lastMsg').textContent = 'connected — unrecognized payload, see console';
    return false;
  }
  const gexByStrike = {};
  if (Array.isArray(body.greeks)) {
    body.greeks.forEach(g => { gexByStrike[g.strike] = Number(g.netGEX ?? 0); });
  }
  SPOT = Number(body.spot ?? SPOT);
  ATM_STRIKE = Number(body.atm ?? ATM_STRIKE);
  OI_VELOCITY = Array.isArray(body.oiVelocity) ? body.oiVelocity : OI_VELOCITY;
  CHAIN = strikesArr.map(r => {
    const peOIChg = Number(r.peChgOI ?? 0);
    const ceOIChg = Number(r.ceChgOI ?? 0);
    return {
      strike: Number(r.strike),
      peOI: Number(r.peOI ?? 0),
      ceOI: Number(r.ceOI ?? 0),
      peOIChg, ceOIChg,
      peOIplus: Math.max(0, peOIChg), peOIminus: Math.max(0, -peOIChg),
      ceOIplus: Math.max(0, ceOIChg), ceOIminus: Math.max(0, -ceOIChg),
      gex: gexByStrike[Number(r.strike)] ?? 0
    };
  }).sort((a,b) => a.strike - b.strike);
  if (ATM_STRIKE == null || isNaN(ATM_STRIKE)) {
    ATM_STRIKE = CHAIN.reduce((best,r) => Math.abs(r.strike-SPOT) < Math.abs(best.strike-SPOT) ? r : best, CHAIN[0]).strike;
  }
  return true;
}

// ─────────────────────────────────────────────────────────────
// WEBSOCKET
// ─────────────────────────────────────────────────────────────
function setBadge(state, text) {
  const el = document.getElementById('connBadge');
  el.className = 'conn-badge ' + state;
  el.textContent = '● ' + text;
}

function connectWS() {
  const input = document.getElementById('wsUrlInput');
  if (!input.value.trim()) input.value = 'ws://' + location.host + '/ws';
  const url = input.value.trim();
  if (!url) return;
  try { if (ws) ws.close(); } catch(e){}
  clearInterval(mockTimer);
  usingMock = false;
  setBadge('down', 'connecting…');

  try {
    ws = new WebSocket(url);
  } catch (e) {
    startMock();
    return;
  }

  ws.onopen = () => setBadge('live', 'live · ' + url);

  ws.onmessage = (evt) => {
    let msg;
    try { msg = JSON.parse(evt.data); } catch(e) { return; }
    if (adaptLiveMessage(msg)) {
      document.getElementById('lastMsg').textContent = 'updated ' + new Date().toLocaleTimeString('en-IN',{hour12:false});
      render();
    }
  };

  ws.onclose = () => {
    setBadge('down', 'disconnected — retrying');
    setTimeout(connectWS, 3000);
    if (!CHAIN.length) startMock();
  };

  ws.onerror = () => { try { ws.close(); } catch(e){} };
}

function reconnect() { connectWS(); }

function startMock() {
  if (usingMock) return;
  usingMock = true;
  setBadge('mock', 'mock data (no WS)');
  seedMock();
  render();
  mockTimer = setInterval(() => { jitterMock(); render(); }, 3000);
}

function seedMock() {
  const strikes = [];
  for (let k = 23600; k <= 24400; k += 50) strikes.push(k);
  SPOT = 23975.25; ATM_STRIKE = 24000;
  CHAIN = strikes.map(strike => {
    const dist = Math.abs(strike - ATM_STRIKE);
    const peOI = Math.round(Math.max(5, 140 - dist/8) * 1e5 * (0.6+Math.random()*0.8));
    const ceOI = Math.round(Math.max(5, 90 + dist/12) * 1e5 * (0.6+Math.random()*0.8));
    const peOIChg = Math.round((Math.random()-0.4) * peOI * 0.4);
    const ceOIChg = Math.round((Math.random()-0.4) * ceOI * 0.4);
    return {
      strike, peOI, ceOI, peOIChg, ceOIChg,
      peOIplus: Math.max(0, peOIChg), peOIminus: Math.max(0, -peOIChg),
      ceOIplus: Math.max(0, ceOIChg), ceOIminus: Math.max(0, -ceOIChg),
      gex: +( (Math.random()-0.45) * 3 ).toFixed(2)
    };
  });
}
function jitterMock() {
  CHAIN.forEach(r => {
    const peOIChg = Math.round((Math.random()-0.45) * r.peOI * 0.08);
    const ceOIChg = Math.round((Math.random()-0.45) * r.ceOI * 0.08);
    r.peOI = Math.max(0, r.peOI + peOIChg);
    r.ceOI = Math.max(0, r.ceOI + ceOIChg);
    r.peOIChg = peOIChg;
    r.ceOIChg = ceOIChg;
    r.peOIplus = Math.max(0, peOIChg); r.peOIminus = Math.max(0, -peOIChg);
    r.ceOIplus = Math.max(0, ceOIChg); r.ceOIminus = Math.max(0, -ceOIChg);
    r.gex = +(r.gex + (Math.random()-0.5)*0.4).toFixed(2);
  });
  SPOT = SPOT + (Math.random()-0.5)*4;
}

// ─────────────────────────────────────────────────────────────
// FORMATTING
// ─────────────────────────────────────────────────────────────
function fmtM(v) {
  const a = Math.abs(v);
  if (a >= 1e7) return (v/1e7).toFixed(2)+'Cr';
  if (a >= 1e5) return (v/1e5).toFixed(2)+'L';
  if (a >= 1e3) return (v/1e3).toFixed(1)+'K';
  return Math.round(v).toString();
}

function getVisibleChain() {
  if (!CHAIN.length) return CHAIN;
  const idx = CHAIN.findIndex(r => r.strike === ATM_STRIKE);
  if (idx < 0) return CHAIN;
  const start = Math.max(0, idx - 10);
  const end = Math.min(CHAIN.length, idx + 10 + 1);
  return CHAIN.slice(start, end);
}

// ─────────────────────────────────────────────────────────────
// BUTTERFLY TAB — full strike-by-strike CE|Strike|PE|PCR table,
// moved here from the main dashboard's #sec-oi-buildup panel. Same
// row logic as OiFlowView.buildOiFlowRows()/buildOiTopMoversStrip()
// in panels-views.js, re-implemented locally since this page is
// standalone and doesn't load that script. sClr/ceOiChgClr equivalents
// are inlined below since C has no CSS-var indirection to piggyback on.
// ─────────────────────────────────────────────────────────────
function getVelByStrike(win) {
  const block = (OI_VELOCITY || []).find(b => b.window === win);
  const out = {};
  if (block && block.rows) block.rows.forEach(r => { out[r.strike] = r; });
  return out;
}

const BFLY_MAX = 220; // px, matches the max bar width used on the main dashboard

// Minimum visible width (px) for the embedded change indicator, so tiny
// changes are never fully invisible against the base bar.
const OI_BAR_MIN_SEG_PX = 3;

// Builds full markup for one bar in the butterfly table. The bar's total
// width ALWAYS equals current OI magnitude (it never grows/shrinks with
// the change) — the change is instead embedded inside that same bar:
//   • OI Chg > 0 → a dashed/striped segment, length = (chg/currentOI) of
//     the bar, anchored at the bar's OUTER/tip edge — "fresh OI added".
//   • OI Chg < 0 → a hollow (see-through) segment of the same proportional
//     length, same anchor — "OI unwound".
//   • OI Chg = 0 → fully solid, no embedded segment.
// Only used in 'combined' mode; 'oi' and 'chg' modes call plainBar() below.
// "Outer" edge differs by track because of how each track aligns its bar:
// the CE track right-aligns its bar (flex-end) so the div's own LEFT edge
// is the outer/tip end (away from the strike column) — anchorRight=false.
// The PE track left-aligns its bar (flex-start) so the div's own RIGHT
// edge is the outer/tip end — anchorRight=true. Passing the wrong side
// would anchor the indicator at the strike (base) end instead.
function plainBar(color, widthPx) {
  const w = Math.max(Math.round(widthPx), 1);
  return `<div class="bfly-bar" style="width:${w}px;box-sizing:border-box;background:${color};border:1px solid ${color};"></div>`;
}

function combinedBar(color, widthPx, currentOI, chgOI, anchorRight) {
  const w = Math.max(Math.round(widthPx), 1);
  const outer = `width:${w}px;box-sizing:border-box;background:${color};border:1px solid ${color};position:relative;overflow:hidden;`;
  if (!chgOI || !currentOI) {
    return `<div class="bfly-bar" style="${outer}"></div>`;
  }
  const frac = Math.min(Math.abs(chgOI) / Math.abs(currentOI), 1);
  const segPx = Math.min(Math.max(Math.round(frac * w), OI_BAR_MIN_SEG_PX), w);
  const sidePos = anchorRight ? 'right:0;' : 'left:0;';
  const hollowBorderSide = anchorRight ? 'border-left' : 'border-right';
  if (chgOI > 0) {
    // Fresh OI added — subtle dashed overlay, base color still shows through.
    return `<div class="bfly-bar" style="${outer}">
      <div style="position:absolute;top:0;bottom:0;${sidePos}width:${segPx}px;background:repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.55) 2px, rgba(255,255,255,0.55) 4px);"></div>
    </div>`;
  }
  // OI unwound — punch a hollow (see-through) segment out of the bar.
  return `<div class="bfly-bar" style="${outer}">
    <div style="position:absolute;top:0;bottom:0;${sidePos}width:${segPx}px;background:${C.bg0};${hollowBorderSide}:1px dashed ${color};box-sizing:border-box;"></div>
  </div>`;
}

function buildBflyRows(chain, velByStrike) {
  if (!chain.length) return '';
  const maxOIval = Math.max(...chain.map(r => Math.max(r.ceOI || 0, r.peOI || 0)), 1);
  const maxDOI = Math.max(...chain.map(r => Math.max(Math.abs(r.ceOIChg || 0), Math.abs(r.peOIChg || 0))), 1);
  const maxVel = Math.max(...chain.map(r => {
    const vr = velByStrike[r.strike] || {};
    return Math.max(Math.abs(vr.ceDOI || 0), Math.abs(vr.peDOI || 0));
  }), 1);

  let html = '';
  chain.forEach(r => {
    let ceV, peV, maxV, signed;
    if (currentMode === 'chg') {
      if (chgWindow === 'all') {
        ceV = r.ceOIChg || 0; peV = r.peOIChg || 0; maxV = maxDOI; signed = true;
      } else {
        const vr = velByStrike[r.strike] || {};
        ceV = vr.ceDOI != null ? vr.ceDOI : 0; peV = vr.peDOI != null ? vr.peDOI : 0; maxV = maxVel; signed = true;
      }
    } else if (currentMode === 'combined') {
      ceV = r.ceOI || 0; peV = r.peOI || 0; maxV = maxOIval; signed = false;
    } else {
      ceV = r.ceOI || 0; peV = r.peOI || 0; maxV = maxOIval; signed = false;
    }
    // CE OI increase = resistance building = red; decrease = green.
    // PE OI increase = support building = green; decrease = red.
    // Unsigned (plain OI) mode always shows CE=red / PE=green, matching
    // the rest of the app's convention (and the canvas tabs' own colors).
    const ceClr = signed ? (ceV >= 0 ? C.red : C.green) : C.red;
    const peClr = signed ? (peV >= 0 ? C.green : C.red) : C.green;
    const cW = Math.max(Math.round(Math.abs(ceV) / maxV * BFLY_MAX), 3);
    const pW = Math.max(Math.round(Math.abs(peV) / maxV * BFLY_MAX), 3);
    const ia = r.strike === ATM_STRIKE;
    const sPCR = r.ceOI > 0 ? (r.peOI || 0) / r.ceOI : 0;
    const pcrClr = sPCR > 1 ? C.green : sPCR < 1 ? C.red : C.txt3;
    const ceLbl = (signed && ceV >= 0 ? '+' : '') + fmtM(ceV);
    const peLbl = (signed && peV >= 0 ? '+' : '') + fmtM(peV);
    const ceBar = currentMode === 'combined' ? combinedBar(ceClr, cW, r.ceOI || 0, r.ceOIChg || 0, false) : plainBar(ceClr, cW);
    const peBar = currentMode === 'combined' ? combinedBar(peClr, pW, r.peOI || 0, r.peOIChg || 0, true) : plainBar(peClr, pW);

    // Combined mode gets an extra chg-OI sub-line under each OI figure
    // (the bars themselves only show the change as an embedded segment,
    // not a number — this makes the exact +/- value readable too).
    // CE increase = resistance building = red; decrease = green.
    // PE increase = support building = green; decrease = red.
    const isCombined = currentMode === 'combined';
    const ceFigCls = 'bfly-fig bfly-fig-ce' + (isCombined ? ' bfly-fig-combined' : '');
    const peFigCls = 'bfly-fig bfly-fig-pe' + (isCombined ? ' bfly-fig-combined' : '');
    let ceFigHtml = `<span class="${ceFigCls}" style="color:${ceClr};">${ceLbl}`;
    let peFigHtml = `<span class="${peFigCls}" style="color:${peClr};">${peLbl}`;
    if (isCombined) {
      const ceChg = r.ceOIChg || 0, peChg = r.peOIChg || 0;
      const ceChgClr = ceChg >= 0 ? C.red : C.green;
      const peChgClr = peChg >= 0 ? C.green : C.red;
      const ceChgLbl = (ceChg >= 0 ? '+' : '') + fmtM(ceChg);
      const peChgLbl = (peChg >= 0 ? '+' : '') + fmtM(peChg);
      ceFigHtml += `<span class="bfly-fig-chg" style="color:${ceChgClr};">${ceChgLbl}</span>`;
      peFigHtml += `<span class="bfly-fig-chg" style="color:${peChgClr};">${peChgLbl}</span>`;
    }
    ceFigHtml += `</span>`;
    peFigHtml += `</span>`;

    html += `<div class="bfly-row${ia ? ' bfly-atm' : ''}">
      ${ceFigHtml}
      <div class="bfly-ce-track">${ceBar}</div>
      <span class="bfly-strike">${r.strike}${ia ? ' ★' : ''}</span>
      <div class="bfly-pe-track">${peBar}</div>
      ${peFigHtml}
      <span class="bfly-pcr" style="color:${pcrClr};">(${sPCR.toFixed(2)})</span>
    </div>`;
  });
  return html;
}

function bflyMoverLabel(kind) {
  // kind: 'ce' | 'pe'
  if (currentMode === 'chg' && chgWindow !== 'all') {
    return kind === 'ce' ? `Biggest CE Vel (${chgWindow}m)` : `Biggest PE Vel (${chgWindow}m)`;
  }
  if (currentMode === 'chg') { // chgWindow === 'all'
    return kind === 'ce' ? 'Biggest CE build' : 'Biggest PE build';
  }
  // 'oi' and 'combined' both rank by plain OI magnitude
  return kind === 'ce' ? 'Biggest CE OI' : 'Biggest PE OI';
}

function buildBflyTopMovers(chain, velByStrike) {
  let ceStrike = null, ceVal = 0, peStrike = null, peVal = 0;
  chain.forEach(r => {
    let ceV, peV;
    if (currentMode === 'chg' && chgWindow !== 'all') {
      const vr = velByStrike[r.strike] || {};
      ceV = vr.ceDOI || 0; peV = vr.peDOI || 0;
    } else if (currentMode === 'chg') { // chgWindow === 'all'
      ceV = r.ceOIChg || 0; peV = r.peOIChg || 0;
    } else {
      ceV = r.ceOI || 0; peV = r.peOI || 0;
    }
    if (ceStrike === null || ceV > ceVal) { ceVal = ceV; ceStrike = r.strike; }
    if (peStrike === null || peV > peVal) { peVal = peV; peStrike = r.strike; }
  });
  if (ceStrike === null && peStrike === null) return '';
  const ceHtml = ceStrike !== null ? `<span style="color:${C.txt3};">${bflyMoverLabel('ce')} <strong style="color:${C.red};">${ceStrike} ▲${fmtM(ceVal)}</strong></span>` : '';
  const peHtml = peStrike !== null ? `<span style="color:${C.txt3};">${bflyMoverLabel('pe')} <strong style="color:${C.green};">${peStrike} ▲${fmtM(peVal)}</strong></span>` : '';
  return [ceHtml, peHtml].filter(Boolean).join(`<span style="color:${C.border};padding:0 8px;">|</span>`);
}

function renderBfly() {
  const bodyEl = document.getElementById('bflyBody');
  const footerEl = document.getElementById('bflyFooter');
  if (!bodyEl) return;
  if (!CHAIN.length) {
    bodyEl.innerHTML = '<div class="bfly-empty">Waiting for data…</div>';
    if (footerEl) footerEl.innerHTML = '';
    return;
  }
  const chain = getVisibleChain();
  const velByStrike = (currentMode === 'chg' && chgWindow !== 'all') ? getVelByStrike(Number(chgWindow)) : {};
  bodyEl.innerHTML = buildBflyRows(chain, velByStrike);
  if (footerEl) footerEl.innerHTML = buildBflyTopMovers(chain, velByStrike);
}

// ─────────────────────────────────────────────────────────────
// VIEW DEFINITIONS (PE-CE Blue bars removed, Red/Left Green/Right order enforced)
// ─────────────────────────────────────────────────────────────
const VIEWS = {
  oi: {
    label: 'OI',
    legend: [['Call OI',C.red],['Put OI',C.green]],
    series: r => [
      { v: r.ceOI, color: C.red },
      { v: r.peOI, color: C.green }
    ]
  },
  oichng: {
    label: 'OI Chng',
    legend: [['Call OI Chng',C.red],['Put OI Chng',C.green]],
    series: r => [
      { v: r.ceOIChg, color: C.red },
      { v: r.peOIChg, color: C.green }
    ]
  },
  gex: {
    label: 'GEX',
    legend: [['Net GEX (+)',C.green],['Net GEX (-)',C.red]],
    series: r => [
      { v: r.gex, color: r.gex >= 0 ? C.green : C.red }
    ]
  }
};

// Builds a VIEWS-shaped object on the fly for a given OI-velocity window
// (5/15/30), since OI_VELOCITY is dynamic data, not a fixed view like OI/GEX.
function velocityView(win) {
  const velByStrike = getVelByStrike(Number(win));
  return {
    label: `OI Vel ${win}m`,
    legend: [['Call OI Vel', C.red], ['Put OI Vel', C.green]],
    series: r => {
      const vr = velByStrike[r.strike] || {};
      return [
        { v: vr.ceDOI || 0, color: C.red },
        { v: vr.peDOI || 0, color: C.green }
      ];
    }
  };
}

// Resolves the current view/mode/window selection down to the VIEWS-shaped
// object drawGrouped()/buildLegend() need. Returns null for 'combined' mode,
// which uses drawCombined() instead of the generic grouped-bar renderer.
function getActiveCanvasView() {
  if (currentView === 'gex') return VIEWS.gex;
  if (currentMode === 'oi') return VIEWS.oi;
  if (currentMode === 'chg') return chgWindow === 'all' ? VIEWS.oichng : velocityView(chgWindow);
  return null; // combined
}

// ─────────────────────────────────────────────────────────────
// DRAW: grouped bar chart (used for OI / OI Chng / GEX)
// ─────────────────────────────────────────────────────────────
function drawGrouped(ctx, w, h, view) {
  const chain = getVisibleChain();
  const pad = { top: 40, bottom: 40, left: 60, right: 60 };
  const chartW = w - pad.left - pad.right;
  const chartH = h - pad.top - pad.bottom;

  let maxV = -Infinity, minV = Infinity;
  chain.forEach(r => view.series(r).forEach(s => {
    if (s.v > maxV) maxV = s.v;
    if (s.v < minV) minV = s.v;
  }));
  if (currentView === 'gex' && !window.__gexLogged) {
    window.__gexLogged = true;
    console.log('[oi-dashboard] GEX values for visible strikes:',
      chain.map(r => ({ strike: r.strike, gex: r.gex })));
  }
  if (maxV <= 0) maxV = 0.01;
  if (minV > 0) minV = 0; 
  maxV *= 1.15; minV *= 1.15;
  const range = maxV - minV || 1;
  const zeroY = pad.top + chartH * (maxV / range);

  const yToPx = v => pad.top + chartH * (1 - (v - minV) / range);

  // grid + axis labels
  ctx.strokeStyle = C.border; ctx.lineWidth = 0.5;
  ctx.font = '9px monospace'; ctx.fillStyle = C.txt3; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  const steps = 6;
  for (let i = 0; i <= steps; i++) {
    const val = minV + (range * i/steps);
    const y = yToPx(val);
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
    ctx.fillText(fmtM(val), pad.left - 8, y);
  }
  // zero line highlighted
  ctx.strokeStyle = 'rgba(255,255,255,0.25)';
  ctx.beginPath(); ctx.moveTo(pad.left, zeroY); ctx.lineTo(w-pad.right, zeroY); ctx.stroke();

  const n = chain.length;
  const slot = chartW / n;

  chain.forEach((r, idx) => {
    const series = view.series(r);
    const groupW = slot * 0.7;
    const barW = groupW / series.length;
    const x0 = pad.left + idx * slot + (slot - groupW) / 2;

    series.forEach((s, si) => {
      const x = x0 + si * barW;
      const y1 = yToPx(0), y2 = yToPx(s.v);
      const top = Math.min(y1, y2), hgt = Math.abs(y2 - y1);
      ctx.fillStyle = s.color;
      ctx.fillRect(x + 1, top, barW - 2, Math.max(hgt, 1));

      if (Math.abs(s.v) > maxV * 0.02) {
        ctx.save();
        ctx.fillStyle = C.txt;
        ctx.font = '8px monospace';
        ctx.translate(x + barW/2, s.v >= 0 ? top - 4 : top + hgt + 4);
        ctx.rotate(-Math.PI/2.2);
        ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
        ctx.fillText(fmtM(s.v), 0, 0);
        ctx.restore();
      }
    });

    // strike label
    ctx.fillStyle = r.strike === ATM_STRIKE ? C.amber : C.txt2;
    ctx.font = r.strike === ATM_STRIKE ? 'bold 10px sans-serif' : '10px sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillText(r.strike, pad.left + idx*slot + slot/2, h - pad.bottom + 8);

    if (r.strike === ATM_STRIKE) {
      ctx.strokeStyle = 'rgba(255,255,255,0.5)';
      ctx.setLineDash([4,4]); ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(pad.left + idx*slot, pad.top);
      ctx.lineTo(pad.left + idx*slot, h - pad.bottom);
      ctx.stroke(); ctx.setLineDash([]);
    }
  });

  ctx.fillStyle = C.txt; ctx.font = '15px sans-serif';
  ctx.textAlign = 'left'; ctx.textBaseline = 'top';
  ctx.fillText('Spot Price: ' + (SPOT ? SPOT.toFixed(2) : '—'), pad.left, 8);
}

// ─────────────────────────────────────────────────────────────
// EXACT MATCH COMBINED VIEW (Calls Left/Green, Puts Right/Red)
// ─────────────────────────────────────────────────────────────
function drawCombined(ctx, w, h) {
  const chain = getVisibleChain();
  const pad = { top: 60, bottom: 40, left: 60, right: 60 }; 
  const chartW = w - pad.left - pad.right;
  const chartH = h - pad.top - pad.bottom;

  let maxV = 1;
  chain.forEach(r => { maxV = Math.max(maxV, r.peOI, r.ceOI); });
  maxV *= 1.15;
  const yToPx = v => pad.top + chartH * (1 - v / maxV);

  // Background grid setup
  ctx.strokeStyle = C.border; ctx.lineWidth = 0.5;
  ctx.font = '9px monospace'; ctx.fillStyle = C.txt3; ctx.textAlign='right'; ctx.textBaseline='middle';
  for (let i = 0; i <= 6; i++) {
    const val = maxV * i/6, y = yToPx(val);
    ctx.beginPath(); ctx.moveTo(pad.left,y); ctx.lineTo(w-pad.right,y); ctx.stroke();
    ctx.fillText(fmtM(val), pad.left-8, y);
  }

  // ─────────────────────────────────────────────────────────────
  // 🎨 FIXED-LENGTH OI BAR WITH EMBEDDED CHANGE INDICATOR
  // The bar's height ALWAYS spans [yTop, base0] — i.e. current OI —
  // regardless of OI Chg. The change is embedded inside that same bar,
  // anchored at the OUTER edge (the bar's tip, yTop — farthest from the
  // 0-line), instead of extending the bar past current OI: a diagonal-
  // striped segment for a build-up (OI Chg > 0), or a hollow/see-through
  // segment for an unwind (OI Chg < 0). A minimum segment height keeps
  // small changes visible.
  // ─────────────────────────────────────────────────────────────
  const OI_BAR_MIN_SEG_PX = 3;
  function drawOiBar(x, barW, base0, yTop, color, currentOI, chgOI) {
    const totalH = base0 - yTop;
    ctx.fillStyle = color;
    ctx.fillRect(x, yTop, barW, totalH);
    if (!chgOI || !currentOI) return;
    const frac = Math.min(Math.abs(chgOI) / Math.abs(currentOI), 1);
    const segH = Math.min(Math.max(Math.round(frac * totalH), OI_BAR_MIN_SEG_PX), totalH);
    const segY = yTop; // anchor at the bar's outer/top edge, not the base
    if (chgOI > 0) {
      // Fresh OI added — subtle diagonal stripes over the base color.
      ctx.save();
      ctx.beginPath(); ctx.rect(x, segY, barW, segH); ctx.clip();
      ctx.strokeStyle = 'rgba(255,255,255,0.55)'; ctx.lineWidth = 1.5;
      for (let sx = x - segH; sx < x + barW + segH; sx += 5) {
        ctx.beginPath(); ctx.moveTo(sx, segY + segH); ctx.lineTo(sx + segH, segY); ctx.stroke();
      }
      ctx.restore();
      ctx.strokeStyle = color; ctx.lineWidth = 1;
      ctx.strokeRect(x + 0.5, segY + 0.5, barW - 1, segH - 1);
    } else {
      // OI unwound — punch a hollow segment out of the bar.
      ctx.clearRect(x, segY, barW, segH);
      ctx.strokeStyle = color; ctx.lineWidth = 1;
      ctx.setLineDash([3, 2]);
      ctx.strokeRect(x + 0.5, segY + 0.5, barW - 1, segH - 1);
      ctx.setLineDash([]);
    }
  }

  const n = chain.length, slot = chartW/n;
  chain.forEach((r, idx) => {
    const groupW = slot*0.6, barW = groupW/2;
    const x0 = pad.left + idx*slot + (slot-groupW)/2;
    const base0 = yToPx(0);

    // ─────────────────────────────────────────────────────────────
    // 🔴 CALL SERIES RENDERING (LEFT BAR) - Resistance
    // Bar height = current CE OI, always. OI Chg is embedded inside it.
    // ─────────────────────────────────────────────────────────────
    const yCeToday = yToPx(r.ceOI);
    ctx.save();
    drawOiBar(x0 + 1, barW - 2, base0, yCeToday, C.red, r.ceOI, r.ceOIChg);
    ctx.restore();

    // ─────────────────────────────────────────────────────────────
    // 🟢 PUT SERIES RENDERING (RIGHT BAR) - Support
    // Bar height = current PE OI, always. OI Chg is embedded inside it.
    // ─────────────────────────────────────────────────────────────
    const cx0 = x0 + barW;
    const yPeToday = yToPx(r.peOI);
    ctx.save();
    drawOiBar(cx0 + 1, barW - 2, base0, yPeToday, C.green, r.peOI, r.peOIChg);
    ctx.restore();

    // ─────────────────────────────────────────────────────────────
    // 🛑 SINGLE-PASS TEXT LABELS
    // ─────────────────────────────────────────────────────────────
    ctx.save();
    ctx.fillStyle = C.txt; ctx.font = '8px monospace';
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';

    // Call Label (bar top = current CE OI)
    ctx.save();
    ctx.translate(x0 + barW/2, yCeToday - 6);
    ctx.rotate(-Math.PI/2.2);
    ctx.fillText(fmtM(r.ceOI), 0, 0);
    ctx.restore();

    // Put Label (bar top = current PE OI)
    ctx.save();
    ctx.translate(cx0 + barW/2, yPeToday - 6);
    ctx.rotate(-Math.PI/2.2);
    ctx.fillText(fmtM(r.peOI), 0, 0);
    ctx.restore();
    
    ctx.restore();

    // Strike Axis highlight labels
    ctx.fillStyle = r.strike === ATM_STRIKE ? C.amber : C.txt2;
    ctx.font = r.strike === ATM_STRIKE ? 'bold 10px sans-serif' : '10px sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillText(r.strike, pad.left + idx*slot + slot/2, h-pad.bottom+8);

    if (r.strike === ATM_STRIKE) {
      ctx.save();
      ctx.strokeStyle = 'rgba(255,255,255,0.5)'; ctx.setLineDash([4,4]); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(pad.left+idx*slot, pad.top); ctx.lineTo(pad.left+idx*slot, h-pad.bottom); ctx.stroke();
      ctx.restore();
    }
  });

  ctx.fillStyle = C.txt; ctx.font = '15px sans-serif';
  ctx.textAlign = 'left'; ctx.textBaseline = 'top';
  ctx.fillText('Spot Price: ' + (SPOT ? SPOT.toFixed(2) : '—'), pad.left, 8);
}

// ─────────────────────────────────────────────────────────────
// RENDER
// ─────────────────────────────────────────────────────────────
function buildLegend() {
  const row = document.getElementById('legendRow');
  if (currentView === 'bar' && currentMode === 'combined') {
    row.innerHTML = `
      <span><span class="sw" style="background:${C.red}"></span>Call OI (bar length = current OI)</span>
      <span><span class="sw" style="background:${C.red}; position:relative; overflow:hidden;"><span style="position:absolute;left:0;top:0;bottom:0;width:40%;background:${C.bg0};border-right:1px dashed ${C.red};"></span></span>Call OI Decrease (hollow segment = amount unwound)</span>
      <span><span class="sw" style="background:${C.red}; position:relative; overflow:hidden;"><span style="position:absolute;left:0;top:0;bottom:0;width:40%;background:repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.55) 2px, rgba(255,255,255,0.55) 4px);"></span></span>Call OI Increase (dashed segment = amount added)</span>
      <span><span class="sw" style="background:${C.green}"></span>Put OI (bar length = current OI)</span>
      <span><span class="sw" style="background:${C.green}; position:relative; overflow:hidden;"><span style="position:absolute;left:0;top:0;bottom:0;width:40%;background:${C.bg0};border-right:1px dashed ${C.green};"></span></span>Put OI Decrease (hollow segment = amount unwound)</span>
      <span><span class="sw" style="background:${C.green}; position:relative; overflow:hidden;"><span style="position:absolute;left:0;top:0;bottom:0;width:40%;background:repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.55) 2px, rgba(255,255,255,0.55) 4px);"></span></span>Put OI Increase (dashed segment = amount added)</span>`;
    return;
  }
  const v = getActiveCanvasView();
  row.innerHTML = v ? v.legend.map(([label,color]) =>
    `<span><span class="sw" style="background:${color}"></span>${label}</span>`).join('') : '';
}

// Shows/hides the mode row (5/15/30/Intraday/OI Chg/OI/Combined) based on
// the current top-level tab. It applies to 'bar' and 'butterfly' only —
// hidden entirely for 'gex', which has no OI/Chg/Combined split.
function updateSubTabVisibility() {
  const modeTabsEl = document.getElementById('modeTabs');
  const showMode = (currentView === 'bar' || currentView === 'butterfly');
  if (modeTabsEl) modeTabsEl.style.display = showMode ? 'flex' : 'none';
}

function render() {
  updateSubTabVisibility();

  const bflyWrap = document.querySelector('.bfly-wrap');
  const chartWrap = document.querySelector('.chart-wrap');
  if (currentView === 'butterfly') {
    if (bflyWrap) bflyWrap.style.display = 'flex';
    if (chartWrap) chartWrap.style.display = 'none';
    renderBfly();
    return;
  }
  if (bflyWrap) bflyWrap.style.display = 'none';
  if (chartWrap) chartWrap.style.display = '';
  document.getElementById('spotDisplay').textContent = SPOT ? SPOT.toFixed(2) : '—';
  buildLegend();

  const canvas = document.getElementById('oiCanvas');
  const wrap = canvas.parentElement;
  const width = wrap.clientWidth - 20;
  const height = wrap.clientHeight - 20;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr; canvas.height = height * dpr;
  canvas.style.width = width + 'px'; canvas.style.height = height + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.fillStyle = C.bg0; ctx.fillRect(0,0,width,height);

  if (!CHAIN.length) {
    ctx.fillStyle = C.txt3; ctx.font = '14px sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText('Waiting for data…', width/2, height/2);
    return;
  }

  if (currentView === 'bar' && currentMode === 'combined') drawCombined(ctx, width, height);
  else drawGrouped(ctx, width, height, getActiveCanvasView());
}

// ─────────────────────────────────────────────────────────────
// TABS + RESIZE + INIT
// ─────────────────────────────────────────────────────────────
// Top-level: Bar Chart / Butterfly / GEX
document.getElementById('tabs').addEventListener('click', e => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  currentView = tab.dataset.view;
  document.querySelectorAll('#tabs .tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  render();
});

// Mode row now holds two kinds of buttons in one line, in this order:
// [5] [15] [30] [Intraday] | [OI Chg]   [OI]   [Combined]
// Clicking a window button (5/15/30/Intraday) implicitly switches mode to
// 'chg' with that window. Clicking OI/Combined switches mode directly and
// leaves chgWindow untouched (it's only read when mode === 'chg' again).
function updateModeRowActiveStates() {
  document.querySelectorAll('#modeTabs .mode-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.mode === currentMode));
  document.querySelectorAll('#modeTabs .win-tab').forEach(t =>
    t.classList.toggle('active', currentMode === 'chg' && t.dataset.win === chgWindow));
  const winGroup = document.getElementById('winGroup');
  if (winGroup) winGroup.classList.toggle('show', currentMode === 'chg');
}

document.getElementById('modeTabs').addEventListener('click', e => {
  const winEl = e.target.closest('.win-tab');
  const modeEl = e.target.closest('.mode-tab');
  if (winEl) {
    currentMode = 'chg';
    chgWindow = winEl.dataset.win;
    updateModeRowActiveStates();
    render();
    return;
  }
  if (modeEl) {
    currentMode = modeEl.dataset.mode;
    updateModeRowActiveStates();
    render();
  }
});
const _isEmbedded = (window.top !== window.self) || !!window.opener;
if (_isEmbedded) {
  setBadge('down', 'waiting for dashboard feed…');
} else {
  connectWS();
}
