// ============================================================
// strategy-view.js
// Split out of panels-views.js. StrategyView — strategy suggestion /
// scenario P&L panel.
// ============================================================

class StrategyView {
  constructor() {
    this.selStratIdx = 0;
    this.selectionTouched = false;
    this.selectionSymbol = null;
  }

  selectStrategy(value){
    this.selStratIdx = parseInt(value, 10) || 0;
    this.selectionTouched = true;
    this.renderStratPayoff();
  }

  renderStratPayoff(){
  if(!_data) return;
  const strats = _data.strategies || [];
  if(!strats.length) return;

  const stratSel  = document.getElementById('strat-select');
  const strikeSel = document.getElementById('strat-strike-select');
  if(!stratSel) return;

  const si   = parseInt(stratSel.value) || 0;
  const s    = strats[si];
  if(!s) return;

  // Populate strike dropdown on strategy change
  _populateStrikeDropdown(s);

  const spot     = _data.spot || _data.spotPrice || 0;
  const atm      = (_data.atm) || spot;
  const lotSize  = _data.lotSize || 50;
  const legs     = s.legs || [];

  // Determine base strike from dropdown (apply ATM offset to legs)
  const selectedStrike = strikeSel.value ? parseFloat(strikeSel.value) : atm;
  const atmDefault     = atm || selectedStrike;
  const offset         = selectedStrike - atmDefault;

  // Build shifted legs
  const shiftedLegs = legs.map(l=>({...l, strike:(l.strike||atmDefault)+offset}));

  // Net credit/debit
  let netVal = (s.netCredit !== undefined && s.netCredit !== null)
    ? parseFloat(s.netCredit)
    : legs.reduce((acc,l)=>acc+(l.action==='SELL'?parseFloat(l.ltp)||0:-(parseFloat(l.ltp)||0)),0);
  netVal = isNaN(netVal) ? 0 : netVal;
  const isCredit = netVal >= 0;

  // ── PAYOFF CURVE ──
  const range    = Math.max(atm * 0.05, 600);
  const center   = selectedStrike || spot || atm;
  const xMin     = center - range;
  const xMax     = center + range;
  const steps    = 200;
  const dx       = (xMax - xMin) / steps;
  let   yMin     = Infinity, yMax = -Infinity;
  const xs=[], ys=[];
  for(let i=0;i<=steps;i++){
    const x = xMin + i*dx;
    const y = shiftedLegs.reduce((acc,l)=>acc+_legPnl(l,x,lotSize),0);
    xs.push(x); ys.push(y);
    if(y<yMin) yMin=y; if(y>yMax) yMax=y;
  }

  // Breakevens — zero crossings
  const breakevens=[];
  for(let i=0;i<ys.length-1;i++){
    if((ys[i]<=0&&ys[i+1]>0)||(ys[i]>=0&&ys[i+1]<0)){
      const frac=-ys[i]/(ys[i+1]-ys[i]);
      breakevens.push(Math.round(xs[i]+frac*(xs[i+1]-xs[i])));
    }
  }

  // Max profit / loss (capped for display)
  const maxProfit = Math.max(...ys);
  const maxLoss   = Math.min(...ys);

  // ── METRICS CARDS ──
  const metricsEl = document.getElementById('strat-metrics');
  if(metricsEl){
    const rupee = (v) => `₹${v >= 0 ? '+' : ''}${fmtI(Math.round(v))}`;
    const beStr = breakevens.length ? breakevens.map(b => '₹' + fmtI(b)).join(', ') : '—';
    
    // Layout and style setups
    const cardStyle = `background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:8px 10px;`;
    const lbStyle   = `font-size:9px; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.07em; margin-bottom:4px;`;
    const vlStyle   = (c) => `font-size:18px; font-weight:800; color:${c}; font-family:var(--mono); letter-spacing:-.02em;`;
    
    setHtmlIfChanged(metricsEl, `
      <div style="${cardStyle}">
        <div style="${lbStyle}">Max Profit</div>
        <div style="${vlStyle('var(--green)')}">
          ${isFinite(maxProfit) && maxProfit < 1e8 ? rupee(maxProfit) : 'Unlimited'}
        </div>
      </div>
      <div style="${cardStyle}">
        <div style="${lbStyle}">Max Loss</div>
        <div style="${vlStyle('var(--red)')}">
          ${isFinite(maxLoss) && maxLoss > -1e8 ? rupee(maxLoss) : 'Unlimited'}
        </div>
      </div>
      <div style="${cardStyle}">
        <div style="${lbStyle}">Breakevens</div>
        <div style="font-size:13px; font-weight:800; color:var(--amber); font-family:var(--mono); line-height: 1.4;">${beStr}</div>
      </div>
      <div style="${cardStyle}">
        <div style="${lbStyle}">Spot</div>
        <div style="${vlStyle('var(--blue)')}">₹${fmtI(Math.round(spot || center))}</div>
      </div>`);
  }
  // ── CANVAS DRAW ──
  // Draws the identical payoff curve onto whichever canvas id is passed in
  // — factored out so the same computed xs/ys/breakevens/metrics above can
  // be painted onto both the inline card canvas (#strat-payoff-canvas) and
  // the expand-modal's canvas (#strat-payoff-canvas-modal, only present in
  // the DOM while the modal is open) without duplicating the drawing math.
  // Called in a loop just below; any canvas id that isn't currently in the
  // DOM (e.g. the modal canvas when the modal is closed) is a no-op.
  const _drawPayoffOnCanvas = (canvasId) => {
  const canvas = document.getElementById(canvasId);
  if(!canvas) return;

  // HiDPI — only resets the canvas (which clears the 2D context and is
  // what caused the visible flash) when the on-screen size actually
  // changed; every other tick just redraws onto the existing surface.
  const W0 = canvas.parentElement.clientWidth - 28;
  const H0 = parseInt(canvas.getAttribute('data-h'),10) || 280;
  const ctx = sizeCanvasIfChanged(canvas, W0, H0);
  const W = W0, H = H0;

  // Dark mode detection
  const isDark = window.matchMedia('(prefers-color-scheme:dark)').matches;
  const C = {
    bg       : isDark ? '#1E2028' : '#F1F3F5',
    grid     : isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
    zero     : isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.12)',
    axisLbl  : isDark ? '#6C757D' : '#868E96',
    spot     : '#339AF0',
    be       : '#FFD43B',
    profitFill:'rgba(32,201,151,0.15)',
    lossFill :'rgba(255,107,107,0.13)',
    line     : '#5BC0DE',
    lineGlow : isDark ? 'rgba(91,192,222,0.6)' : 'rgba(51,154,240,0.5)',
  };

  const PAD = {l:52, r:16, t:16, b:36};
  const PW = W - PAD.l - PAD.r;
  const PH = H - PAD.t - PAD.b;

  // Scale helpers
  const padY = (yMax - yMin) * 0.08 || 500;
  const yLo  = yMin - padY, yHi = yMax + padY;
  const toX  = (v) => PAD.l + (v - xMin)/(xMax - xMin) * PW;
  const toY  = (v) => PAD.t + (1 - (v - yLo)/(yHi - yLo)) * PH;
  const zeroY= toY(0);

  ctx.clearRect(0,0,W,H);

  // ── Grid lines ──
  const yTicks = 6;
  for(let i=0;i<=yTicks;i++){
    const yv = yLo + (yHi-yLo)*i/yTicks;
    const py = toY(yv);
    ctx.strokeStyle = Math.abs(yv)<(yHi-yLo)*0.03 ? C.zero : C.grid;
    ctx.lineWidth   = Math.abs(yv)<(yHi-yLo)*0.03 ? 1 : 0.7;
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(PAD.l,py); ctx.lineTo(W-PAD.r,py); ctx.stroke();
    // Y axis labels
    ctx.fillStyle   = C.axisLbl;
    ctx.font        = `10px 'JetBrains Mono',monospace`;
    ctx.textAlign   = 'right';
    ctx.textBaseline= 'middle';
    const label = Math.abs(yv)>=1000 ? (yv>=0?'+':'')+Math.round(yv/1000)+'k'
                                     : (yv>=0?'+':'')+Math.round(yv);
    ctx.fillText('₹'+label, PAD.l-6, py);
  }

  // X axis ticks
  const xTicks = 8;
  ctx.font = `9px 'JetBrains Mono',monospace`;
  ctx.textAlign='center'; ctx.textBaseline='top';
  for(let i=0;i<=xTicks;i++){
    const xv = xMin + (xMax-xMin)*i/xTicks;
    const px = toX(xv);
    ctx.strokeStyle=C.grid; ctx.lineWidth=0.7;
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(px,PAD.t); ctx.lineTo(px,H-PAD.b); ctx.stroke();
    ctx.fillStyle=C.axisLbl;
    ctx.fillText('₹'+fmtI(Math.round(xv)), px, H-PAD.b+5);
  }

  // ── Profit / loss fill areas ──
  // Profit fill (above zero)
  ctx.beginPath();
  ctx.moveTo(toX(xs[0]), zeroY);
  xs.forEach((x,i)=>{ const py=toY(ys[i]); ctx.lineTo(toX(x), py<zeroY?py:zeroY); });
  ctx.lineTo(toX(xs[xs.length-1]), zeroY);
  ctx.closePath();
  ctx.fillStyle = C.profitFill;
  ctx.fill();

  // Loss fill (below zero)
  ctx.beginPath();
  ctx.moveTo(toX(xs[0]), zeroY);
  xs.forEach((x,i)=>{ const py=toY(ys[i]); ctx.lineTo(toX(x), py>zeroY?py:zeroY); });
  ctx.lineTo(toX(xs[xs.length-1]), zeroY);
  ctx.closePath();
  ctx.fillStyle = C.lossFill;
  ctx.fill();

  // ── Zero line ──
  ctx.strokeStyle='rgba(255,255,255,0.18)'; ctx.lineWidth=1;
  ctx.setLineDash([4,3]);
  ctx.beginPath(); ctx.moveTo(PAD.l,zeroY); ctx.lineTo(W-PAD.r,zeroY); ctx.stroke();
  ctx.setLineDash([]);

  // ── Payoff curve with glow ──
  function drawCurve(lw, clr, shadow, blur){
    ctx.save();
    if(shadow){ ctx.shadowColor=shadow; ctx.shadowBlur=blur||8; }
    ctx.strokeStyle=clr; ctx.lineWidth=lw; ctx.lineJoin='round'; ctx.lineCap='round';
    ctx.beginPath();
    xs.forEach((x,i)=>{ const px=toX(x),py=toY(ys[i]); i===0?ctx.moveTo(px,py):ctx.lineTo(px,py); });
    ctx.stroke();
    ctx.restore();
  }
  drawCurve(4, C.lineGlow, C.lineGlow, 12);
  drawCurve(2, C.line);

  // ── Spot vertical dashed line ──
  if(spot && spot>=xMin && spot<=xMax){
    const sx=toX(spot);
    ctx.strokeStyle=C.spot; ctx.lineWidth=1.2; ctx.setLineDash([5,3]);
    ctx.beginPath(); ctx.moveTo(sx,PAD.t); ctx.lineTo(sx,H-PAD.b); ctx.stroke();
    ctx.setLineDash([]);
    // label
    ctx.fillStyle=C.spot; ctx.font=`bold 10px 'Inter',sans-serif`;
    ctx.textAlign='center'; ctx.textBaseline='top';
    ctx.fillText('Spot', sx, PAD.t+2);
  }

  // ── Breakeven markers ──
  breakevens.forEach(be=>{
    if(be<xMin||be>xMax) return;
    const bx=toX(be);
    ctx.strokeStyle=C.be; ctx.lineWidth=1; ctx.setLineDash([3,2]);
    ctx.beginPath(); ctx.moveTo(bx,PAD.t); ctx.lineTo(bx,H-PAD.b); ctx.stroke();
    ctx.setLineDash([]);
    // dot on zero
    ctx.fillStyle=C.be;
    ctx.beginPath(); ctx.arc(bx,zeroY,4,0,Math.PI*2); ctx.fill();
    // label
    ctx.font=`bold 9px 'JetBrains Mono',monospace`;
    ctx.textAlign='center'; ctx.textBaseline='bottom';
    ctx.fillText('₹'+fmtI(be), bx, zeroY-6);
  });
  }; // <-- close _drawPayoffOnCanvas()

  // Paint the inline card canvas always; paint the modal's canvas too, but
  // only when it's actually in the DOM (the modal markup uses the same
  // id whether open or closed — no need to check .open, since drawing
  // onto a hidden canvas is harmless and keeps it current for the moment
  // the person opens it).
  _drawPayoffOnCanvas('strat-payoff-canvas');
  _drawPayoffOnCanvas('strat-payoff-canvas-modal');

  // ── LEG PILLS ──
  const legsEl = document.getElementById('strat-legs-row');
  if(legsEl){
    // Pin label
    const nameTag=`<span style="font-size:11px;font-weight:800;color:var(--muted);margin-right:2px;">📌 ${s.name||'Strategy'}</span>`;
    // Execute-whole-strategy button — fires one place_order per leg through
    // the same ptDispatchOrder() path the option-chain quick popover and
    // the main panel's "Place Order" button already use, so confirmations/
    // toasts/pending rows/portfolio refresh all behave identically no
    // matter where the order originated.
    const execAllBtn = `<button type="button" onclick="ptExecuteStrategy()" title="Place all legs of this strategy as paper orders"
      style="cursor:pointer;font-size:10px;font-weight:800;padding:3px 9px;border-radius:5px;
      background:var(--accent,#3b82f6);color:#fff;margin-left:8px;border:0;">▶ Execute Strategy</button>`;
    const symbolForLegs = _data.symbol || '';
    const expiryForLegs = s.expiry || _data.expiry || '';
    // BUGFIX: this pill used to show the raw, unresolved expiryForLegs —
    // so a cached strategy suggestion still carrying a rolled-off date
    // (e.g. "24-Jun" after that expiry has already passed) displayed as
    // if it were a live, tradeable expiry, with nothing on screen hinting
    // that "▶ Execute Strategy" was about to submit a dead contract.
    // Resolve it the same way execution does (ptExecuteStrategy /
    // ptExecuteLeg both call ptResolveStrategyExpiry) so the pill always
    // reflects what will actually be sent to the backend.
    const expiryForLegsReal = ptResolveStrategyExpiry(expiryForLegs);
    // Per-leg expiry: prefer the leg's own `expiry` field if the backend
    // ever sends one (forward-compatible), else fall back to the
    // strategy-level expiry above. A calendar spread is DEFINED by its
    // legs having different expiries at the same strike — collapsing
    // everything to one expiry, or omitting it entirely, silently turns a
    // calendar spread into something else. Detect that mismatch and flag
    // it instead of hiding it.
    const legExpiries = shiftedLegs.map(l=>l.expiry || expiryForLegs);
    const uniqueExpiries = [...new Set(legExpiries.filter(Boolean))];
    const isMultiExpiry = uniqueExpiries.length > 1;
    const staleExpiryWarn = (expiryForLegs && expiryForLegsReal && expiryForLegs !== expiryForLegsReal)
      ? `<span title="Strategy expiry ${expiryForLegs} is no longer live — will execute against ${expiryForLegsReal} instead" style="font-size:10px;font-weight:800;padding:2px 7px;border-radius:4px;
         background:rgba(255,107,107,.16);color:var(--red,#ff6b6b);margin-left:6px;">⚠ Rolled → ${ptFmtExpiry(expiryForLegsReal)}</span>`
      : '';
    const expiryPill = expiryForLegsReal
      ? `<span title="${expiryForLegsReal}" style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;
         background:rgba(59,130,246,.15);color:var(--accent,#3b82f6);margin-left:6px;">📅 ${ptFmtExpiry(expiryForLegsReal)}</span>`
      : '';
    const multiExpiryWarn = isMultiExpiry
      ? `<span title="Legs use different expiries: ${uniqueExpiries.join(', ')}" style="font-size:10px;font-weight:800;padding:2px 7px;border-radius:4px;
         background:rgba(237,161,0,.16);color:var(--amber,#eda100);margin-left:6px;">⚠ Multi-expiry</span>`
      : '';
    const pillsHtml = shiftedLegs.map(l=>{
      const isBuy=l.action==='BUY';
      const ac=isBuy?'var(--green)':'var(--red)';
      const acBg=isBuy?'rgba(32,201,151,0.12)':'rgba(255,107,107,0.12)';
      const border=isBuy?'rgba(32,201,151,0.35)':'rgba(255,107,107,0.35)';
      const ltp=parseFloat(l.ltp)||0;
      const legType=(l.type||'').toUpperCase();
      const legExpiry = l.expiry || expiryForLegs;
      // Real date used for execution/pricing; legExpiry above stays as the
      // raw NEAR/FAR label for display purposes.
      const legExpiryReal = ptResolveStrategyExpiry(legExpiry);
      // Only show a per-leg expiry tag when it differs from the strategy's
      // headline expiry (calendar spreads) — otherwise the shared expiry
      // pill above already covers every leg and repeating it per pill
      // would just be noise.
      const legExpTag = (l.expiry && l.expiry !== expiryForLegs)
        ? `<span style="color:var(--amber,#eda100);font-size:9px;" title="${l.expiry} → ${legExpiryReal}">(${ptFmtExpiry(l.expiry)})</span>`
        : '';
      const execBtn = `<button type="button" onclick="ptExecuteLeg('${symbolForLegs}','${legExpiryReal}',${l.strike},'${legType}','${l.action}',${l.lots||1},${ltp})"
        title="Execute this leg as a paper order (expiry ${legExpiryReal||'—'})"
        style="cursor:pointer;font-size:9px;font-weight:800;padding:1px 5px;border-radius:4px;
        background:${ac};color:#0b0d12;margin-left:2px;border:0;" aria-label="Execute ${l.action} ${fmtI(l.strike)} ${legType} leg">▶</button>`;
      return `<span style="display:inline-flex;align-items:center;gap:4px;
        padding:5px 10px;border-radius:6px;border:1px solid ${border};
        background:${acBg};font-family:var(--mono);font-size:11px;font-weight:700;">
        <span style="color:${ac};">${l.action}</span>
        <span style="color:var(--txt);">${fmtI(l.strike)} ${legType}</span>
        ${legExpTag}
        <span style="color:var(--muted);">@</span>
        <span style="color:${ac};">₹${fmtN(ltp,2)}</span>
        ${execBtn}
      </span>`;
    }).join('');
    setHtmlIfChanged(legsEl, nameTag + expiryPill + staleExpiryWarn + multiExpiryWarn + execAllBtn + pillsHtml);
  }
}

  _afterRenderStratPayoff(){
  // Small delay to let innerHTML settle
  setTimeout(()=>{
    if(document.getElementById('strat-select')) renderStratPayoff();
  }, 80);
}

  _populateStrikeDropdown(strat){
  const sel = document.getElementById('strat-strike-select');
  if(!sel) return;
  const strikes = (strat.legs||[]).map(l=>l.strike).filter(Boolean);
  const atm = (_data && _data.atm) || (strikes.length ? strikes[0] : 0);
  // unique sorted strikes from chain near ATM
  let chainStrikes = [];
  if(_data && _data.chain){
    chainStrikes = _data.chain.map(r=>r.strike).filter(Boolean).sort((a,b)=>a-b);
  } else if(strikes.length){
    // fallback: ±10 strikes around ATM in steps of 50
    const step = 50;
    for(let i=-10;i<=10;i++) chainStrikes.push(atm + i*step);
  }
  const selectedVal = (_selStrike!=null && chainStrikes.includes(_selStrike)) ? _selStrike : atm;
  // Same diff pattern as the global expiry <select>: only rebuild the
  // option list (which visibly flickers/resets on every rebuild) when the
  // strike list itself changed; otherwise just keep the current selection
  // in sync without touching the DOM.
  const optionsKey = chainStrikes.join('|') + '@' + atm;
  if(sel.dataset.optionsKey !== optionsKey){
    sel.innerHTML = chainStrikes.map(s=>{
      const label = s === atm ? `${fmtI(s)} (ATM)` : fmtI(s);
      return `<option value="${s}" ${s===selectedVal?'selected':''}>${label}</option>`;
    }).join('');
    sel.dataset.optionsKey = optionsKey;
  } else if(sel.value !== String(selectedVal)){
    sel.value = selectedVal;
  }
}
}
