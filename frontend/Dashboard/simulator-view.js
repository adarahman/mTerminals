// ============================================================
// simulator-view.js
// Split out of panels-views.js. SimulatorView — What-If simulator panel.
// Depends on dashboard-thresholds.js (INST_THRESHOLDS); load after it.
// ============================================================

class SimulatorView {
  constructor() {
    this.simSpotOverride = null;
    this.simIvOverride = null;
    this.simVelOverride = null;
    this.simDealerOverride = null;
    this.gexScenarioDirty = false;
    this.simState = {
  spot: 0, iv: 15, vel: 1.2, dealerBias: 0,
  greeks: [], atm: 0, step: 50, volOiRatios: {}
};
  }

  simInit() {
  if (!_data) return;
  var d = _data;
  var ctx = d.ctx || {};
  // d.greeks/d.atm/d.spot/d.atmIV are the fields applyExpirySelection()
  // actually rewrites when the global expiry dropdown changes; d.ctx is a
  // static snapshot from the very first payload and never updates, so
  // falling back to it (instead of preferring it) is what let the whole
  // Institutional Simulator + Scenario Controls freeze on expiry switch.
  this.simState.greeks = d.greeks || [];
  this.simState.atm = d.atm || ctx.atm || 0;
  this.simState.spot = d.spot || ctx.spot || 0;
  this.simState.iv = parseFloat(d.atmIV || ctx.baseIv || 15);
  this.simState.step = this.simState.greeks.length > 1 ?
    (this.simState.greeks[1].strike - this.simState.greeks[0].strike) : 50;
  this.simState.volOiRatios = d.volOiRatios || {};
  this._syncPristineControlsToLive();
  this.simUpdate();
}

  // A live tick may move the reference spot/IV. Untouched controls follow
  // that live reference; once the user changes a control its override is
  // preserved until Reset Scenario. This keeps live data current without
  // erasing explicit scenario inputs.
  _syncPristineControlsToLive() {
  var spotEl = document.getElementById('sim-spot-slider');
  var ivEl = document.getElementById('sim-iv-slider');
  var velEl = document.getElementById('sim-vel-slider');
  if (spotEl && this.simSpotOverride == null) {
    var spot = Math.min(parseFloat(spotEl.max), Math.max(parseFloat(spotEl.min), this.simState.spot));
    spotEl.value = spot;
  }
  if (ivEl && this.simIvOverride == null) ivEl.value = this.simState.iv;
  if (velEl && this.simVelOverride == null) velEl.value = this.simState.vel;
}

  resetScenario() {
  this.simSpotOverride = null;
  this.simIvOverride = null;
  this.simVelOverride = null;
  this.simDealerOverride = null;
  this.gexScenarioDirty = false;
  var dealerEl = document.getElementById('sim-dealer-sel');
  if (dealerEl) dealerEl.value = '0';
  this._syncPristineControlsToLive();
  this.simUpdate();
}

  // oninput fires on every pixel of slider drag; without coalescing, each
  // of those events triggered a canvas redraw + vol-grid rebuild + table
  // innerHTML rebuild. Collapsed to one _simUpdateNow() per animation
  // frame — still reads the live slider value when the frame fires, same
  // pattern as scheduleRender() elsewhere.
  simUpdate() {
  if (this._simUpdateScheduled) return;
  this._simUpdateScheduled = true;
  var self = this;
  // requestAnimationFrame callbacks get throttled/fully paused by the
  // browser the instant this tab is hidden or loses focus (switching to a
  // different browser tab or app). setTimeout keeps firing in the
  // background (throttled to roughly 1/sec, never fully paused), so use
  // that instead whenever the document isn't visible; rAF is still
  // preferred while visible since it aligns with actual paint timing.
  var schedule = (typeof document !== 'undefined' && document.hidden)
    ? function (fn) { setTimeout(fn, 250); }
    : requestAnimationFrame;
  schedule(function(){
    self._simUpdateScheduled = false;
    self._simUpdateNow();
  });
}

  _simUpdateNow() {
  var spotEl = document.getElementById('sim-spot-slider');
  var ivEl   = document.getElementById('sim-iv-slider');
  var velEl  = document.getElementById('sim-vel-slider');
  var selEl  = document.getElementById('sim-dealer-sel');
  if (!spotEl) return;

  // Fall back to last-known simState values (rather than throwing) if a
  // scenario-control element is missing from the current template — a
  // dropped control (e.g. the Vol/OI Velocity slider) should degrade that
  // one control, not blank the entire chart/vol-grid/table render.
  var simSpot = parseFloat(spotEl.value);
  var simIV   = ivEl  ? parseFloat(ivEl.value)  : (this.simState.iv  || 15);
  var simVel  = velEl ? parseFloat(velEl.value) : (this.simState.vel || 1.2);
  var simBias = selEl ? parseFloat(selEl.value) : (this.simState.dealerBias || 0);
  // Track intent explicitly. Rendering can quantize a live value to the
  // slider step, but that must not turn the untouched live chart into a
  // scenario. Only a user edit marks GEX as scenario-adjusted.
  var isLiveBaseline = !this.gexScenarioDirty;

  var setText = function(id, value) {
    var node = document.getElementById(id);
    if (node) node.textContent = value;
  };
  setText('sim-gex-title', isLiveBaseline ? 'Live Net GEX Profile ($B)' : 'Scenario-Adjusted Net GEX Profile ($B)');
  setText('sim-gex-scope', isLiveBaseline ? '(Live Baseline)' : '(Scenario-Adjusted)');
  setText('sim-regime-label', isLiveBaseline ? 'Live Dealer Regime' : 'Scenario Dealer Regime');
  setText('sim-stat-gex-label', isLiveBaseline ? 'Live Net GEX ($B)' : 'Scenario Net GEX ($B)');
  setText('sim-stat-flip-label', isLiveBaseline ? 'Live Gamma Flip' : 'Scenario-Adjusted Gamma Flip');

  var spotValEl = document.getElementById('sim-spot-val');
  if (spotValEl) spotValEl.textContent = fmtI(Math.round(simSpot));
  var ivValEl = document.getElementById('sim-iv-val');
  if (ivValEl) ivValEl.textContent = fmtN(simIV, 1);
  var velValEl = document.getElementById('sim-vel-val');
  if (velValEl) velValEl.textContent = fmtN(simVel, 1);

  var ivRatio  = simIV / (this.simState.iv || simIV);
  var vannaAdj = 1.0 + Math.abs(simBias) * 0.15 * ivRatio;

  var simGEX = this.simState.greeks.map(function(g) {
    var adjGex = (g.netGEX || 0) * ivRatio * vannaAdj;
    // g.iv from the greeks payload is a decimal fraction (0.40 = 40%), unlike
    // every other IV field in this app (ceIV/peIV/atmIV are already percent,
    // e.g. 37.87). Convert here so simRenderTable's "fmtN(iv,1) + '%'" shows
    // real values instead of everything collapsing toward 0.x% after rounding.
    var ivPct = g.iv != null ? g.iv * 100 : null;
    return { strike: g.strike, netGEX: adjGex, iv: ivPct, cDelta: g.cDelta, pDelta: g.pDelta, cGamma: g.cGamma };
  });

  // computeNetGEX/computeGammaFlip (metrics.js, IA redesign step 6) —
  // simGEX is the slider-adjusted array (Scenario-Adjusted scope, see
  // metrics.js's own doc comment), a legitimately different input from
  // the live-figure call sites elsewhere, not a duplicate of them.
  var totalGEX = computeNetGEX(simGEX);
  var vannaMultiplier = 1.0 + Math.abs(totalGEX) / (30 * ivRatio);
  var flipRow = computeGammaFlip(simGEX, simSpot);

  var gexEl = document.getElementById('sim-stat-gex');
  if (gexEl) {
    gexEl.textContent = fmtN(totalGEX, 2);
    gexEl.style.color = totalGEX >= 0 ? 'var(--blue)' : 'var(--red)';
    var sub = gexEl.nextElementSibling;
    if (sub) sub.textContent = (isLiveBaseline ? 'Live: ' : 'Scenario: ')
      + (totalGEX >= 0 ? 'long gamma (dampens)' : 'short gamma (amplifies)');
  }
  var vannaEl = document.getElementById('sim-stat-vanna');
  if (vannaEl) vannaEl.textContent = fmtN(vannaMultiplier, 2);
  var flipEl = document.getElementById('sim-stat-flip');
  if (flipEl) flipEl.textContent = flipRow ? fmtI(flipRow.strike) : '--';

  var needlePct = Math.max(0, Math.min(100, 50 + (totalGEX / 25) * 50));
  var needle = document.getElementById('sim-regime-needle');
  if (needle) needle.style.left = needlePct.toFixed(1) + '%';
  var regimeVal = document.getElementById('sim-regime-val');
  if (regimeVal) {
    var label, color;
    if      (totalGEX >  10) { label = 'Long Gamma';  color = 'var(--green)'; }
    else if (totalGEX < -10) { label = 'Short Gamma'; color = 'var(--red)'; }
    else if (totalGEX >   2) { label = 'Mild Long';   color = 'var(--blue)'; }
    else if (totalGEX <  -2) { label = 'Mild Short';  color = 'var(--amber)'; }
    else                     { label = 'Balanced';     color = 'var(--txt2)'; }
    regimeVal.textContent = label;
    regimeVal.style.color = color;
  }

  this.simRenderGEXChart(simGEX, simSpot, flipRow ? flipRow.strike : 0);
  this.simRenderVolGrid(simGEX, simVel);
  // Strike Detail is live/canonical and intentionally receives no scenario values.
}

  simRenderGEXChart(gexData, simSpot, flipStrike) {
  if (!gexData.length) return;
  // Draws the identical GEX bar chart onto whichever canvas/annot id pair
  // is passed in — factored out (same reasoning as the Strategy Payoff
  // chart's _drawPayoffOnCanvas in renderStratPayoff above) so it can
  // paint both the inline card canvas (#sim-gex-canvas) and the
  // expand-modal's canvas (#sim-gex-canvas-modal) from one pass over
  // gexData, instead of duplicating this whole draw. Called in a loop
  // just below the function body; a canvas id not currently in the DOM
  // (e.g. the modal canvas while closed) is a no-op.
  var _drawGexOnCanvas = (canvasId, annotId) => {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;
  var W = canvas.parentElement.clientWidth - 28;
  var H = parseInt(canvas.getAttribute('data-h'),10) || 220;
  // Same fix as the Strategy Payoff chart: only reset the canvas surface
  // (which clears the 2D context) when the on-screen size actually
  // changed, instead of doing it unconditionally on every live tick.
  var ctx = sizeCanvasIfChanged(canvas, W, H);

  var cs = getComputedStyle(document.documentElement);
  var clrBlue  = cs.getPropertyValue('--blue').trim()   || '#339AF0';
  var clrRed   = cs.getPropertyValue('--red').trim()    || '#FA5252';
  var clrBorder= cs.getPropertyValue('--border').trim() || 'rgba(0,0,0,0.07)';
  var clrTxt3  = cs.getPropertyValue('--txt3').trim()   || '#868E96';
  var clrGreen = cs.getPropertyValue('--green').trim()  || '#12B886';

  ctx.clearRect(0, 0, W, H);

  var PAD_L = 46, PAD_R = 12, PAD_T = 20, PAD_B = 36;
  var chartW = W - PAD_L - PAD_R;
  var chartH = H - PAD_T - PAD_B;

  var vals = gexData.map(function(g) { return g.netGEX; });
  var absVals = vals.map(function(v) { return Math.abs(v); });
  var maxV = Math.max.apply(null, absVals.concat([1]));
  var yRange = maxV * 1.25;

  // Grid lines
  var gridLines = 5;
  ctx.strokeStyle = clrBorder;
  ctx.lineWidth = 1;
  for (var gi = 0; gi <= gridLines; gi++) {
    var gy = PAD_T + (gi / gridLines) * chartH;
    ctx.beginPath(); ctx.moveTo(PAD_L, gy); ctx.lineTo(W - PAD_R, gy); ctx.stroke();
    var gv = yRange - (gi / gridLines) * yRange * 2;
    ctx.fillStyle = clrTxt3;
    ctx.font = '9px JetBrains Mono, monospace';
    ctx.textAlign = 'right';
    ctx.fillText(fmtN(gv, 1), PAD_L - 4, gy + 3);
  }

  var zeroY = PAD_T + (yRange / (yRange * 2)) * chartH;
  ctx.strokeStyle = clrBorder;
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(PAD_L, zeroY); ctx.lineTo(W - PAD_R, zeroY); ctx.stroke();

  var barW = Math.max(4, Math.floor((chartW / gexData.length) - 2));
  var barGap = chartW / gexData.length;

  for (var bi = 0; bi < gexData.length; bi++) {
    var g = gexData[bi];
    var bx = PAD_L + bi * barGap + (barGap - barW) / 2;
    var pct = g.netGEX / yRange;
    var barH = Math.abs(pct) * (chartH / 2);
    var by = g.netGEX >= 0 ? zeroY - barH : zeroY;
    ctx.fillStyle = g.netGEX >= 0 ? (clrGreen + 'AA') : (clrBlue + 'AA');
    ctx.strokeStyle = g.netGEX >= 0 ? clrGreen : clrBlue;
    ctx.lineWidth = 1;
    ctx.fillRect(bx, by, barW, Math.max(barH, 1));
    ctx.strokeRect(bx, by, barW, Math.max(barH, 1));
  }

  // Persistent strike-axis labels: the chart must remain readable without
  // requiring a mouse hover. Space labels according to the available width
  // and always retain both ends of the displayed strike range.
  var maxStrikeLabels = Math.max(2, Math.floor(chartW / 62));
  var strikeLabelStep = Math.max(1, Math.ceil((gexData.length - 1) / (maxStrikeLabels - 1)));
  ctx.font = '9px JetBrains Mono, monospace';
  ctx.fillStyle = clrTxt3;
  ctx.strokeStyle = clrBorder;
  ctx.lineWidth = 1;
  for (var li = 0; li < gexData.length; li += strikeLabelStep) {
    var labelIdx = li;
    var labelX = PAD_L + labelIdx * barGap + barGap / 2;
    ctx.beginPath();
    ctx.moveTo(labelX, H - PAD_B);
    ctx.lineTo(labelX, H - PAD_B + 4);
    ctx.stroke();
    ctx.textAlign = labelIdx === 0 ? 'left' : 'center';
    ctx.fillText(fmtI(gexData[labelIdx].strike), labelX, H - PAD_B + 15);
  }
  if ((gexData.length - 1) % strikeLabelStep !== 0) {
    var lastIdx = gexData.length - 1;
    var lastX = PAD_L + lastIdx * barGap + barGap / 2;
    ctx.beginPath();
    ctx.moveTo(lastX, H - PAD_B);
    ctx.lineTo(lastX, H - PAD_B + 4);
    ctx.stroke();
    ctx.textAlign = 'right';
    ctx.fillText(fmtI(gexData[lastIdx].strike), lastX, H - PAD_B + 15);
  }

  // Spot marker
  var spotIdx = 0;
  var minDist = Infinity;
  for (var si = 0; si < gexData.length; si++) {
    var d = Math.abs(gexData[si].strike - simSpot);
    if (d < minDist) { minDist = d; spotIdx = si; }
  }
  var spotX = PAD_L + spotIdx * barGap + barGap / 2;
  ctx.strokeStyle = clrBlue;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 3]);
  ctx.beginPath(); ctx.moveTo(spotX, PAD_T); ctx.lineTo(spotX, H - PAD_B); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = clrBlue;
  ctx.font = '9px Inter, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('SPOT:' + fmtI(Math.round(simSpot)), spotX, PAD_T - 4);

  // Flip zone
  if (flipStrike) {
    var fi = 0;
    var minDist = Infinity;

    for (var fii = 0; fii < gexData.length; fii++) {
      var dist = Math.abs(gexData[fii].strike - flipStrike);

      if (dist < minDist) {
        minDist = dist;
        fi = fii;
      }
    }

        if (fi >= 0) {

      var flipX;
      var idx = fi;

      // Bracket the flip strike correctly: `idx` is only the NEAREST
      // strike to flipStrike, which can sit on either side of it. The old
      // logic always paired [idx, idx+1] and assumed flipStrike was >=
      // gexData[idx].strike, which silently failed (and snapped to the
      // nearest bar's center instead of interpolating) whenever the true
      // flip value actually fell between the PREVIOUS strike and this one.
      var leftIdx, rightIdx;
      if (flipStrike < gexData[idx].strike && idx > 0) {
        leftIdx = idx - 1;
        rightIdx = idx;
      } else if (idx < gexData.length - 1) {
        leftIdx = idx;
        rightIdx = idx + 1;
      } else {
        leftIdx = rightIdx = idx;
      }

      var left = gexData[leftIdx];
      var right = gexData[rightIdx];

      if (rightIdx !== leftIdx && flipStrike >= left.strike && flipStrike <= right.strike) {

        var ratio =
          (flipStrike-left.strike) /
          (right.strike-left.strike);

        flipX =
          PAD_L +
          (leftIdx + ratio) * barGap +
          barGap/2;

      } else {

        flipX =
          PAD_L +
          idx * barGap +
          barGap/2;
      }


      // draw flip marker here
      ctx.strokeStyle = clrRed;
      ctx.beginPath();
      ctx.moveTo(flipX, PAD_T);
      ctx.lineTo(flipX, H-PAD_B);
      ctx.stroke();

      ctx.fillStyle = clrRed;
      ctx.fillText('FLIP ' + fmtI(Math.round(flipStrike)), flipX, zeroY+16);

    } // fi

  } // flipStrike
  ctx.fillStyle = clrTxt3;
  ctx.font = '9px Inter, sans-serif';
  ctx.textAlign = 'right';
  ctx.fillText('Strike \u2192', W - PAD_R, H - 2);

  // Tooltip
  canvas.onmousemove = function(e) {
    var rect = canvas.getBoundingClientRect();
    var mx = e.clientX - rect.left;
    var idx = Math.floor((mx - PAD_L) / barGap);
    var annot = document.getElementById(annotId);
    if (annot && idx >= 0 && idx < gexData.length) {
      var gp = gexData[idx];
      annot.style.display = 'block';
      annot.innerHTML = '<strong>Net GEX Profile ($B) \u2191 ' + fmtN(gp.netGEX, 2) + '</strong><br>Strike \u2192 ' + fmtI(gp.strike);
      annot.style.left = Math.min(mx + 10, W - 180) + 'px';
    }
  };
    canvas.onmouseleave = function() {
    var annot = document.getElementById(annotId);
    if (annot) annot.style.display = 'none';
  };

  }; // <-- close _drawGexOnCanvas()

  _drawGexOnCanvas('sim-gex-canvas', 'sim-annot');
  _drawGexOnCanvas('sim-gex-canvas-modal', 'sim-annot-modal');

} // <-- close simRenderGEXChart()

simRenderVolGrid(gexData, simVel) {
  var el = document.getElementById('sdt-voi-grid');
  if (!el) return;
  var ratios = this.simState.volOiRatios || {};
  var atm = this.simState.atm;
  var step = this.simState.step || 50;

  // Same per-strike OI lookup used by the Strike Detail table below, so
  // the ratio bars up here and the OI figures down there always agree.
  var oiByStrike = {};
  ((_data && _data.chain) || []).forEach(function(r) {
    oiByStrike[r.strike] = { ce: r.ceOI || 0, pe: r.peOI || 0 };
  });

  // ── Near (ATM ±INST_NEAR_BAND_STRIKES) vs Far band, rendered as two
  // separately-scored sections instead of one flat "8 nearest strikes"
  // pool. Each band keeps its own bar-height scaling (maxCE/maxPE) and its
  // own INST_THRESHOLDS.blockVal, since a "block" print reads differently
  // close to spot vs out in the wings — see the INST_THRESHOLDS comment
  // above the OiFlowView class for the rationale.
  function buildPool(strikes) {
    var ceRows = strikes.map(function(g) {
      var r = ratios[String(g.strike)] || { ce: 0 };
      var oi = (oiByStrike[g.strike] || {}).ce || 0;
      return { strike: g.strike, val: (r.ce || 0) * simVel, oi: oi };
    }).sort(function(a, b) { return b.val - a.val; });

    var peRows = strikes.map(function(g) {
      var r = ratios[String(g.strike)] || { pe: 0 };
      var oi = (oiByStrike[g.strike] || {}).pe || 0;
      return { strike: g.strike, val: (r.pe || 0) * simVel, oi: oi };
    }).sort(function(a, b) { return b.val - a.val; });

    return { ceRows: ceRows, peRows: peRows };
  }

  function barRow(strike, val, max, color, oi, band) {
    var pct = Math.min(Math.round((val / max) * 100), 100);
    var isBlock = val > INST_THRESHOLDS[band].blockVal;
    return '<div class="sim-vol-bar-row">' +
      '<span class="sim-vol-bar-label" style="color:var(--txt2);">' + fmtI(strike) + '</span>' +
      '<div class="sim-vol-bar-track"><div class="sim-vol-bar-fill" style="width:' + pct + '%;background:' + color + ';"></div></div>' +
      '<span class="sim-vol-bar-val" style="color:' + (isBlock ? 'var(--amber)' : 'var(--txt3)') + ';">' + fmtN(val, 2) + (isBlock ? ' &#9650;' : '') + '</span>' +
      '<span class="sim-vol-bar-oi" style="color:var(--txt3);font-size:9px;margin-left:6px;white-space:nowrap;">OI ' + fmtK(oi) + '</span>' +
      '</div>';
  }

  function buildSection(label, strikes, band) {
    if (!strikes.length) return '';
    var pool = buildPool(strikes);
    var maxCE = Math.max.apply(null, pool.ceRows.map(function(r) { return r.val; }).concat([0.01]));
    var maxPE = Math.max.apply(null, pool.peRows.map(function(r) { return r.val; }).concat([0.01]));
    var n = band === 'near' ? 5 : 4; // far band gets a slightly tighter top-N so it doesn't dwarf near

    var ceHtml = '<div class="sim-vol-card"><div class="sim-vol-card-title" style="color:var(--ce);">CE Vol/OI Ratio</div>';
    pool.ceRows.slice(0, n).forEach(function(r) { ceHtml += barRow(r.strike, r.val, maxCE, 'var(--ce)', r.oi, band); });
    ceHtml += '</div>';

    var peHtml = '<div class="sim-vol-card"><div class="sim-vol-card-title" style="color:var(--pe);">PE Vol/OI Ratio</div>';
    pool.peRows.slice(0, n).forEach(function(r) { peHtml += barRow(r.strike, r.val, maxPE, 'var(--pe)', r.oi, band); });
    peHtml += '</div>';

    return '<div class="sim-vol-band-section" style="margin-bottom:10px;width:100%;grid-column:1/-1;">' +
      '<div style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">' + label + '</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;align-items:start;width:100%;">' + ceHtml + peHtml + '</div>' +
      '</div>';
  }

  var nearStrikes = gexData.filter(function(g) { return instBandFor(g.strike, atm, step) === 'near'; });
  var farStrikes  = gexData.filter(function(g) { return instBandFor(g.strike, atm, step) === 'far'; });

  var html = buildSection('Near ATM (\u00B1' + INST_NEAR_BAND_STRIKES + ' strikes)', nearStrikes, 'near') +
             buildSection('Far Strikes (beyond \u00B1' + INST_NEAR_BAND_STRIKES + ')', farStrikes, 'far');

  setHtmlIfChanged(el, html || '<div style="padding:8px;color:var(--txt3);font-size:11px;">No strike data available.</div>');

  // ── Summary line (always visible, above the click-to-open chart) ──
  // Same "count + strongest" rollup shape as the Institutional Activity
  // Crux card, scoped to this panel's own block-print flags instead of
  // Crux's OI-standout flags — the two scan by strike but aren't
  // measuring the same thing, so they're allowed to disagree. Recomputes
  // over the same near/far pools buildSection already built, rather than
  // parsing its HTML output back apart.
  // Writes into #oi-flow-block-summary inside the Vol/OI Velocity card.
  // Block prints are a velocity-derived result, so the summary stays with
  // the control that owns the scan instead of the general Capital Flow card.
  var summaryEl = document.getElementById('oi-flow-block-summary');
  if (summaryEl) {
    var flaggedAll = [];
    [['near', nearStrikes], ['far', farStrikes]].forEach(function(pair) {
      var band = pair[0], pool = buildPool(pair[1]);
      pool.ceRows.forEach(function(r) { if (r.val > INST_THRESHOLDS[band].blockVal) flaggedAll.push({ strike: r.strike, side: 'CE', ratio: r.val / INST_THRESHOLDS[band].blockVal }); });
      pool.peRows.forEach(function(r) { if (r.val > INST_THRESHOLDS[band].blockVal) flaggedAll.push({ strike: r.strike, side: 'PE', ratio: r.val / INST_THRESHOLDS[band].blockVal }); });
    });
    if (flaggedAll.length) {
      var strongest = flaggedAll.slice().sort(function(a, b) { return b.ratio - a.ratio; })[0];
      summaryEl.innerHTML = '<span style="color:var(--amber);font-weight:700;">' + flaggedAll.length + ' block print' + (flaggedAll.length === 1 ? '' : 's') + '</span> flagged \u2022 strongest ' + fmtI(strongest.strike) + ' ' + strongest.side;
    } else {
      summaryEl.innerHTML = 'No block-size prints detected right now.';
    }
  }
}

  simRenderTable(gexData, simSpot, simIV) {
  // #sdt-rows lives inside the Strike Detail Report modal
  // (strike-detail-report-modal in DashboardPro.html) — not always in the
  // DOM if that modal markup is ever absent, hence the null-guard on `el`
  // below. This function always computes the rows/stats regardless, so
  // the modal is current the instant it's opened rather than waiting for
  // the next tick.
  var el = document.getElementById('sdt-rows');
  var atm = this.simState.atm;
  var step = this.simState.step || 50;
  var ratios = this.simState.volOiRatios || {};
  // Real per-strike open interest lives on the chain rows (ceOI/peOI), not
  // on volOiRatios (which only carries ce_vol/pe_vol — traded volume — plus
  // the vol/OI ratio itself). The table was previously showing
  // ceVol+peVol under the "Open Interest" header, which is volume, not OI.
  var oiByStrike = {};
  ((_data && _data.chain) || []).forEach(function(r) {
    oiByStrike[r.strike] = { ce: r.ceOI || 0, pe: r.peOI || 0, ceChg: r.ceChgOI || 0, peChg: r.peChgOI || 0 };
  });

  // ── Near (ATM ±INST_NEAR_BAND_STRIKES) and Far strikes are now rendered
  // as two separately-scored sections rather than one flat "10 nearest"
  // window. This does two things the old single window couldn't:
  //   1. Far-band strikes actually show up at all (previously the table
  //      only ever displayed the 10 strikes closest to ATM, so a flagged
  //      strike further out was invisible here even though the crux card
  //      already scans the full chain for it).
  //   2. Each band's "Institutional Accumulation" call is judged against
  //      its OWN median OI, not one median blended across both — near-ATM
  //      strikes carry naturally heavier OI, so blending would either make
  //      near-band flags too easy or far-band flags nearly impossible.
  var nearAll = gexData.filter(function(g) { return instBandFor(g.strike, atm, step) === 'near'; });
  var farAll  = gexData.filter(function(g) { return instBandFor(g.strike, atm, step) === 'far'; });

  nearAll.sort(function(a, b) { return b.strike - a.strike; });
  // Far band can span the whole rest of the chain — cap to the 12 strikes
  // with the largest resting OI so the far section stays a scan, not a
  // scroll, then present those in strike order.
  var farRanked = farAll.slice().sort(function(a, b) {
    var oa = oiByStrike[a.strike] || { ce: 0, pe: 0 };
    var ob = oiByStrike[b.strike] || { ce: 0, pe: 0 };
    return (ob.ce + ob.pe) - (oa.ce + oa.pe);
  }).slice(0, 12);
  farRanked.sort(function(a, b) { return b.strike - a.strike; });

  function medianOIOf(rows) {
    var totals = rows.map(function(g) {
      var s = oiByStrike[g.strike] || { ce: 0, pe: 0 };
      return s.ce + s.pe;
    }).sort(function(a, b) { return a - b; });
    return totals.length ? totals[Math.floor(totals.length / 2)] : 0;
  }

  // NEW (institutional strike-detail redesign): scaled against the rows
  // actually rendered in each section (nearAll / farRanked), not the
  // whole far band, so the bar comparison stays meaningful for what's on
  // screen — same reasoning as maxOI in chain-view-models.js's
  // buildOiCombinedBarViewModel for the dense-chain expand panel.
  function maxOIOf(rows) {
    var vals = rows.map(function(g) {
      var s = oiByStrike[g.strike] || { ce: 0, pe: 0 };
      return s.ce + s.pe;
    }).concat([1]);
    return Math.max.apply(null, vals);
  }

  // Scales the new diverging CE/PE bar: largest SINGLE-LEG OI in the
  // section (not CE+PE combined like maxOIOf above), so a strike that's
  // almost entirely CE (or almost entirely PE) doesn't get artificially
  // shrunk against a combined total it doesn't actually share.
  function maxLegOIOf(rows) {
    var vals = rows.map(function(g) {
      var s = oiByStrike[g.strike] || { ce: 0, pe: 0 };
      return Math.max(s.ce || 0, s.pe || 0);
    }).concat([1]);
    return Math.max.apply(null, vals);
  }

  var nearMedianOI = medianOIOf(nearAll);
  var farMedianOI  = medianOIOf(farAll);
  var nearMaxOI = maxOIOf(nearAll);
  var farMaxOI  = maxOIOf(farRanked);
  var nearMaxLegOI = maxLegOIOf(nearAll);
  var farMaxLegOI  = maxLegOIOf(farRanked);

  

  // NEW: single combined-OI bar (total OI length + a dashed/hollow overlay
  // for the dominant leg's ΔOI). Redesigned per feedback: a solid filled
  // bar in the leg's red/green read as a directional call, which OI size
  // alone isn't — so the bar itself is now a dotted cyan track (matching
  // the design spec's "Large OI: Bright Cyan" mapping), independent of
  // which leg dominates; red/green stays reserved for the CE/PE text next
  // to it. Dotted fill instead of solid reads lighter against the dark
  // theme. Width is 100% of its cell (not a fixed px) so it fills
  // whatever the Open Interest column gives it instead of floating in
  // leftover space.
  // Diverging Call OI / Put OI bar — replaces the old single dotted-cyan
  // "total OI + dominant-leg overlay" bar. CE grows leftward from the
  // center divider, PE grows rightward from it, each scaled against
  // maxLegOI (the largest single-leg OI in the section currently
  // rendered), so the two sides stay visually comparable row-to-row.
  // Uses the project's own --ce/--pe tokens (same colors as the chain
  // table and OI Butterfly Bars) rather than the reference layout's
  // blue/red, so this reads as the same CE/PE convention everywhere else
  // on the dashboard.
  function oiBarHtml(ceOI, peOI, maxLegOI) {
    var cPct = maxLegOI > 0 ? Math.min(100, (ceOI / maxLegOI) * 100) : 0;
    var pPct = maxLegOI > 0 ? Math.min(100, (peOI / maxLegOI) * 100) : 0;
    return '<div style="display:flex;align-items:center;height:13px;min-width:0;">' +
      '<div class="sdt-ce-track" style="flex:1;"><div class="sdt-ce-bar" style="width:' + cPct.toFixed(1) + '%;"></div></div>' +
      '<div class="sdt-bar-divider"></div>' +
      '<div class="sdt-pe-track" style="flex:1;"><div class="sdt-pe-bar" style="width:' + pPct.toFixed(1) + '%;"></div></div>' +
    '</div>';
  }

  var maxPainStrike = (_data && _data.maxPain != null) ? Number(_data.maxPain) : null;
  
  const nearStructure = marketStructureLabels(
    nearAll,
    atm,
    oiByStrike,
    maxPainStrike
);

const farStructure = marketStructureLabels(
    farRanked,
    atm,
    oiByStrike,
    maxPainStrike
);
  function rowHtml(g, band, medianOI, maxOI, maxLegOI, structure) {
    var isAtm = g.strike === atm;
    var rawRatio = ratios[String(g.strike)];
    var hasRatioData = !!rawRatio;
    var ratio = rawRatio || { ce: 0, pe: 0, ce_vol: 0, pe_vol: 0 };
    var oiSplit = oiByStrike[g.strike] || { ce: 0, pe: 0 };
    var totalOI = oiSplit.ce + oiSplit.pe;
    var volRatio = totalOI > 0 ? ((ratio.ce || 0) + (ratio.pe || 0)) / 2 : 0;
    // volRatio is on the same scale as the CE/PE Vol/OI Ratio panel above
    // (roughly 0-100+, "volume as % of OI"), not a 0-1 fraction. Large
    // resting size (OI well above this band's own median, by a
    // band-specific margin) plus low-enough turnover (also band-specific)
    // reads as institutional accumulation; if we never received a ratio
    // for this strike, that's missing data, not a "0% turnover" reading,
    // so it must not default into the institutional branch.
    var th = INST_THRESHOLDS[band];
    var isInst = hasRatioData && totalOI > medianOI * th.oiMult && volRatio < th.volRatioMax;
    var netDelta = Math.abs((g.cDelta || 0) - Math.abs(g.pDelta || 0));

    var oiDominant = oiSplit.ce >= oiSplit.pe ? 'CE' : 'PE';
    // NEW: the dominant leg's own ΔOI still drives the ΔOI Today column
    // and the Smart Money direction read below; the bar itself no longer
    // needs a dominant-leg concept now that it shows both legs directly.
    var oiDomChg = oiDominant === 'CE' ? (oiSplit.ceChg || 0) : (oiSplit.peChg || 0);
    var chgClr = oiDomChg > 0 ? 'var(--green)' : oiDomChg < 0 ? 'var(--amber)' : 'var(--txt3)';
    var dist = g.strike - atm;
    var distText = isAtm ? 'ATM' : (dist > 0 ? '+' + dist : String(dist));
    var badge = smartMoneyBadge(hasRatioData, isInst, oiDomChg, totalOI, volRatio, th);
    var struct = structure[g.strike];

return '<div class="sdt-row' +
       (isAtm ? ' atm-row' : '') +
       '" data-strike="' + g.strike + '">' +
      '<span style="font-family:var(--mono);font-weight:' + (isAtm ? 700 : 400) + ';color:' + (isAtm ? 'var(--txt)' : 'var(--txt2)') + ';">' + fmtI(g.strike) + '</span>' +
      '<span style="font-family:var(--mono);color:' + (isAtm ? 'var(--green)' : 'var(--txt3)') + ';font-weight:' + (isAtm ? 700 : 400) + ';">' + distText + '</span>' +
      '<span class="sdt-oi-cellwrap">' +
        '<span class="sdt-oi-fig" style="color:var(--ce);text-align:right;">' + fmtK(oiSplit.ce) + '</span>' +
        oiBarHtml(oiSplit.ce, oiSplit.pe, maxLegOI) +
        '<span class="sdt-oi-fig" style="color:var(--pe);">' + fmtK(oiSplit.pe) + '</span>' +
      '</span>' +
      '<span style="display:flex;align-items:center;gap:4px;white-space:nowrap;">' +
        '<span style="width:6px;height:6px;border-radius:50%;flex:0 0 auto;background:' + (oiDominant === 'CE' ? 'var(--ce)' : 'var(--pe)') + ';" title="' + oiDominant + ' is the larger leg at this strike"></span>' +
        '<span style="font-family:var(--mono);color:' + chgClr + ';">' + (oiDomChg >= 0 ? '+' : '\u2212') + fmtK(Math.abs(oiDomChg)) + '</span>' +
      '</span>' +
      '<span style="text-align:right;color:var(--amber);font-family:var(--mono);">' + fmtN(g.iv || simIV, 1) + '%</span>' +
      '<span style="text-align:right;font-family:var(--mono);color:var(--txt);">' + fmtN(netDelta, 2) + '</span>' +
      '<span style="display:flex;align-items:center;gap:4px;white-space:nowrap;">' +
        '<span>' + badge.dot + '</span>' +
        '<span style="color:' + badge.color + ';font-weight:600;">' + badge.label + '</span>' +
      '</span>' +
      '<span style="white-space:nowrap;color:' + (struct ? struct.color : 'var(--txt3)') + ';">' + (struct ? struct.text : '') + '</span>' +
    '</div>';
  }

  function sectionHtml(label, rows, band, medianOI, maxOI, maxLegOI, structure) {
    if (!rows.length) return '';
    var body = rows.map(function(g) { return rowHtml(g, band, medianOI, maxOI, maxLegOI, structure); }).join('');
    return '<div class="sim-strike-band-section" style="margin-bottom:10px;">' +
      '<div style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.06em;padding:4px 10px;">' + label + '</div>' +
      body + '</div>';
  }

  var html = sectionHtml('Near ATM (\u00B1' + INST_NEAR_BAND_STRIKES + ' strikes)', nearAll, 'near', nearMedianOI, nearMaxOI, nearMaxLegOI, nearStructure) +
             sectionHtml('Far Strikes (beyond \u00B1' + INST_NEAR_BAND_STRIKES + ', top 12 by OI)', farRanked, 'far', farMedianOI, farMaxOI, farMaxLegOI, farStructure);
  if (el) setHtmlIfChanged(el, html || '<div style="padding:12px;color:var(--txt3);font-size:11px;">No strike data available.</div>');

  // ── Stat summary bar (ATM strike / spot / near-band total OI / near-band
  // PCR) — lives in the Strike Detail Report modal's header
  // (DashboardPro.html). Updated here on every call so it's never stale
  // relative to the rows below it, regardless of whether the modal is
  // currently open.
  var atmEl = document.getElementById('sdt-stat-atm');
  var spotEl = document.getElementById('sdt-stat-spot');
  var totalOiEl = document.getElementById('sdt-stat-totaloi');
  var pcrEl = document.getElementById('sdt-stat-pcr');
  {
    var nearTotalCe = 0, nearTotalPe = 0;
    nearAll.forEach(function(g) {
      var s = oiByStrike[g.strike] || { ce: 0, pe: 0 };
      nearTotalCe += s.ce || 0;
      nearTotalPe += s.pe || 0;
    });
    var nearTotalOI = nearTotalCe + nearTotalPe;
    var nearPCR = nearTotalCe > 0 ? nearTotalPe / nearTotalCe : 0;
    if (atmEl) atmEl.textContent = fmtI(atm);
    if (spotEl) spotEl.textContent = (simSpot != null) ? Number(simSpot).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '\u2014';
    if (totalOiEl) totalOiEl.textContent = fmtK(nearTotalOI);
    if (pcrEl) {
      pcrEl.textContent = fmtN(nearPCR, 2);
      pcrEl.style.color = nearPCR >= 1 ? 'var(--green)' : nearPCR >= 0.8 ? 'var(--amber)' : 'var(--red)';
    }
  }
}
}

// ModalManager (open/close + Escape handling for all seven full-screen
// modals) has moved to Panels/modal-manager.js — see that file's header
// for the split rationale. Load it after this file and before
// dashboard.js (DashboardPro.html script order already updated).
