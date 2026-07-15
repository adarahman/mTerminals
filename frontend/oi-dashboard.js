// ═══════════════════════════════════════════════════════════════════
// OI DASHBOARD (oi_dashboard.html) — extracted from that file's inline
// <script> block, unchanged, on 2026-07-12. Logic untouched: WebSocket
// connect/reconnect, parent postMessage feed, mock-data fallback, and
// the three canvas chart renderers (grouped bars for OI/OI Chng/GEX,
// striped combined view). See oi_dashboard.html for the markup this
// binds to (#oiCanvas, #tabs, #legendRow, #connBadge, #wsUrlInput, etc).
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
let currentView = 'oi';
let SPOT = null, ATM_STRIKE = null;
let CHAIN = [];          // [{strike, peOI, ceOI, peOIChg, ceOIChg, peOIplus, peOIminus, ceOIplus, ceOIminus, gex}]
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
    if (raw.ceDOI !== undefined) {
      existing.ceOIChg = Number(raw.ceDOI ?? 0);
      existing.ceOIplus = Math.max(0, existing.ceOIChg);
      existing.ceOIminus = Math.max(0, -existing.ceOIChg);
    }
    if (raw.peDOI !== undefined) {
      existing.peOIChg = Number(raw.peDOI ?? 0);
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
  CHAIN = strikesArr.map(r => {
    const peOIChg = Number(r.peDOI ?? 0);
    const ceOIChg = Number(r.ceDOI ?? 0);
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
  chain.forEach(r => {
    const pePrev = r.peOI - r.peOIChg, cePrev = r.ceOI - r.ceOIChg;
    maxV = Math.max(maxV, r.peOI, pePrev, r.ceOI, cePrev);
  });
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
  // 🎨 STRIPED PATTERN GENERATORS FOR INCREASES (+)
  // ─────────────────────────────────────────────────────────────
  const createStripePattern = (color) => {
    const pCanvas = document.createElement('canvas');
    pCanvas.width = 8; pCanvas.height = 8;
    const pCtx = pCanvas.getContext('2d');
    pCtx.strokeStyle = color; pCtx.lineWidth = 1.5;
    pCtx.beginPath();
    pCtx.moveTo(0, 8); pCtx.lineTo(8, 0);
    pCtx.stroke();
    return ctx.createPattern(pCanvas, 'repeat');
  };
  const greenStripes = createStripePattern('#2ECC71');
  const redStripes = createStripePattern('#E74C3C');

  const n = chain.length, slot = chartW/n;
  chain.forEach((r, idx) => {
    const groupW = slot*0.6, barW = groupW/2;
    const x0 = pad.left + idx*slot + (slot-groupW)/2;
    const base0 = yToPx(0);

    // ─────────────────────────────────────────────────────────────
    // 🔴 CALL SERIES RENDERING (LEFT BAR) - Resistance
    // ─────────────────────────────────────────────────────────────
    const cePrev = r.ceOI - r.ceOIChg;
    const yCePrev = yToPx(cePrev);
    const yCeToday = yToPx(r.ceOI);

    ctx.save();
    if (r.ceOIChg >= 0) {
      // Net Increase: Standing historical base is solid red
      ctx.fillStyle = C.red;
      ctx.fillRect(x0 + 1, yCePrev, barW - 2, base0 - yCePrev);
      
      // Daily Increase Segment: Clean diagonal striped block
      if (r.ceOIChg > 0) {
        const hgt = Math.abs(yCePrev - yCeToday);
        ctx.fillStyle = redStripes;
        ctx.fillRect(x0 + 1, yCeToday, barW - 2, hgt);
        
        ctx.strokeStyle = C.red; ctx.lineWidth = 1;
        ctx.strokeRect(x0 + 1, yCeToday, barW - 2, hgt);
      }
    } else {
      // Net Decrease: Remaining active positions are solid red
      ctx.fillStyle = C.red;
      ctx.fillRect(x0 + 1, yCeToday, barW - 2, base0 - yCeToday);
      
      // Liquidated Space: Clean hollow frame with matching red outline
      const hgt = Math.abs(yCeToday - yCePrev);
      ctx.strokeStyle = C.red; ctx.lineWidth = 1.5;
      ctx.strokeRect(x0 + 1, yCePrev, barW - 2, hgt);
    }
    ctx.restore();

    // ─────────────────────────────────────────────────────────────
    // 🟢 PUT SERIES RENDERING (RIGHT BAR) - Support
    // ─────────────────────────────────────────────────────────────
    const cx0 = x0 + barW;
    const pePrev = r.peOI - r.peOIChg;
    const yPePrev = yToPx(pePrev);
    const yPeToday = yToPx(r.peOI);

    ctx.save();
    if (r.peOIChg >= 0) {
      // Net Increase: Standing historical base is solid green
      ctx.fillStyle = C.green;
      ctx.fillRect(cx0 + 1, yPePrev, barW - 2, base0 - yPePrev);
      
      // Daily Increase Segment: Clean diagonal striped block
      if (r.peOIChg > 0) {
        const hgt = Math.abs(yPePrev - yPeToday);
        ctx.fillStyle = greenStripes;
        ctx.fillRect(cx0 + 1, yPeToday, barW - 2, hgt);
        
        ctx.strokeStyle = C.green; ctx.lineWidth = 1;
        ctx.strokeRect(cx0 + 1, yPeToday, barW - 2, hgt);
      }
    } else {
      // Net Decrease: Remaining active positions are solid green
      ctx.fillStyle = C.green;
      ctx.fillRect(cx0 + 1, yPeToday, barW - 2, base0 - yPeToday);
      
      // Liquidated Space: Clean hollow frame with matching green outline
      const hgt = Math.abs(yPeToday - yPePrev);
      ctx.strokeStyle = C.green; ctx.lineWidth = 1.5;
      ctx.strokeRect(cx0 + 1, yPePrev, barW - 2, hgt);
    }
    ctx.restore();

    // ─────────────────────────────────────────────────────────────
    // 🛑 SINGLE-PASS TEXT LABELS
    // ─────────────────────────────────────────────────────────────
    ctx.save();
    ctx.fillStyle = C.txt; ctx.font = '8px monospace';
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';

    // Call Label (Pinnacle height)
    const ceMaxY = Math.min(yCeToday, yCePrev);
    ctx.save();
    ctx.translate(x0 + barW/2, ceMaxY - 6);
    ctx.rotate(-Math.PI/2.2);
    ctx.fillText(fmtM(r.ceOI), 0, 0);
    ctx.restore();

    // Put Label (Pinnacle height)
    const peMaxY = Math.min(yPeToday, yPePrev);
    ctx.save();
    ctx.translate(cx0 + barW/2, peMaxY - 6);
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
  if (currentView === 'combined') {
    row.innerHTML = `
      <span><span class="sw" style="background:${C.red}"></span>Call OI</span>
      <span><span class="sw" style="border:1.5px solid ${C.red}; background:transparent"></span>Call OI Decrease</span>
      <span><span class="sw" style="background:repeating-linear-gradient(45deg, transparent, transparent 2px, #E74C3C 2px, #E74C3C 4px); border:1px solid ${C.red}"></span>Call OI Increase</span>
      <span><span class="sw" style="background:${C.green}"></span>Put OI</span>
      <span><span class="sw" style="border:1.5px solid ${C.green}; background:transparent"></span>Put OI Decrease</span>
      <span><span class="sw" style="background:repeating-linear-gradient(45deg, transparent, transparent 2px, #2ECC71 2px, #2ECC71 4px); border:1px solid ${C.green}"></span>Put OI Increase</span>`;
  } else {
    const v = VIEWS[currentView];
    row.innerHTML = v.legend.map(([label,color]) =>
      `<span><span class="sw" style="background:${color}"></span>${label}</span>`).join('');
  }
}

function render() {
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

  if (currentView === 'combined') drawCombined(ctx, width, height);
  else drawGrouped(ctx, width, height, VIEWS[currentView]);
}

// ─────────────────────────────────────────────────────────────
// TABS + RESIZE + INIT
// ─────────────────────────────────────────────────────────────
document.getElementById('tabs').addEventListener('click', e => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  currentView = tab.dataset.view;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  render();
});

const _isEmbedded = (window.top !== window.self) || !!window.opener;
if (_isEmbedded) {
  setBadge('down', 'waiting for dashboard feed…');
} else {
  connectWS();
}
