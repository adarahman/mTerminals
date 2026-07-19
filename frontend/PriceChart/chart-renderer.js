// ============================================================
// chart-renderer.js
// Canvas rendering component for price chart
// Handles all drawing operations: grid, axes, candles, lines, overlays
// ============================================================

class ChartRenderer {
  constructor() {
    this._lastRenderCtx = null;
  }

  // Time mapping for gap compression (overnight/weekend gaps)
  buildTimeMap(points, gapThreshold, gapCap) {
    if (!points.length) {
      return { toVirtual: t => t, toReal: v => v, virtualStart: 0, virtualEnd: 0 };
    }
    const segs = [{ real: points[0].t, virtual: 0, gapBefore: false }];
    let vCursor = 0;
    for (let i = 1; i < points.length; i++) {
      const delta = points[i].t - points[i - 1].t;
      const isGap = delta > gapThreshold;
      vCursor += isGap ? gapCap : delta;
      segs.push({ real: points[i].t, virtual: vCursor, gapBefore: isGap });
    }
    const first = segs[0], last = segs[segs.length - 1];

    const findSeg = (arr, key, val) => {
      let lo = 0, hi = arr.length - 1;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (arr[mid][key] <= val) lo = mid; else hi = mid;
      }
      return [arr[lo], arr[hi]];
    };

    const toVirtual = (t) => {
      if (t <= first.real) return first.virtual - (first.real - t);
      if (t >= last.real) return last.virtual + (t - last.real);
      const [a, b] = findSeg(segs, 'real', t);
      const realDelta = b.real - a.real;
      if (realDelta <= 0) return a.virtual;
      return a.virtual + ((t - a.real) / realDelta) * (b.virtual - a.virtual);
    };
    const toReal = (v) => {
      if (v <= first.virtual) return first.real - (first.virtual - v);
      if (v >= last.virtual) return last.real + (v - last.virtual);
      const [a, b] = findSeg(segs, 'virtual', v);
      const virtDelta = b.virtual - a.virtual;
      if (virtDelta <= 0) return a.real;
      return a.real + ((v - a.virtual) / virtDelta) * (b.real - a.virtual);
    };
    const isInGap = (t) => {
      if (t <= first.real || t >= last.real) return false;
      const [, b] = findSeg(segs, 'real', t);
      return b.gapBefore === true;
    };
    return { toVirtual, toReal, isInGap, virtualStart: first.virtual, virtualEnd: last.virtual };
  }

  // Main render method
  render(canvas, series, values, settings, rangeConfig, zoomState, dataBounds) {
    const W0 = canvas.parentElement.clientWidth - 24;
    const H0 = Math.max(160, canvas.parentElement.clientHeight);
    const ctx = sizeCanvasIfChanged(canvas, W0, H0);
    const W = W0, H = H0;
    const PAD = { l: 54, r: 12, t: 12, b: 22 };
    const PW = W - PAD.l - PAD.r, PH = H - PAD.t - PAD.b;

    ctx.clearRect(0, 0, W, H);

    if (!series || !series.length) return { ctx, PAD, PW, PH, W, H, xScale: null, yScale: null, tmap: null, y0: 0, y1: 0, C };

    const isDark = window.matchMedia('(prefers-color-scheme:dark)').matches;
    const C = {
      grid: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
      axisLbl: isDark ? '#6C757D' : '#868E96',
      line: settings.lineColor,
      lineGlow: isDark ? 'rgba(51,154,240,0.5)' : 'rgba(51,154,240,0.35)',
      up: settings.upColor,
      down: settings.downColor,
      sma: '#FFD43B',
      ema: '#B197FC',
      vwap: settings.vwapColor,
    };

    // Calculate Y-axis scale based on recent data
    const FIT_LOOKBACK = 40;
    const fitStart = Math.max(0, series.length - FIT_LOOKBACK);
    const fitSeries = series.slice(fitStart);
    const fitValues = values.slice(fitStart);
    const yMin = Math.min(...(settings.type === 'candle' ? fitSeries.map(c => c.l) : fitValues));
    const yMax = Math.max(...(settings.type === 'candle' ? fitSeries.map(c => c.h) : fitValues));
    const padY = (yMax - yMin) * 0.1 || 1;
    const y0 = yMin - padY, y1 = yMax + padY;

    // Build time map with gap compression
    // 1.5x the bar width is enough headroom above normal bar-to-bar
    // spacing (including intraday's continuous 9:15-15:30 session) but
    // well under a weekend/holiday gap — e.g. for '1d' bars (bucketMs =
    // 1 day) this is 1.5 days, comfortably below a ~2.75-day Fri-close to
    // Mon-open weekend gap, so it actually gets flagged and collapsed.
    // The old 4x multiplier gave daily bars a 4-day threshold, which a
    // normal weekend gap never crossed, leaving real (uncompressed) dead
    // space between Friday's and Monday's candles.
    const GAP_THRESHOLD = Math.max(rangeConfig.bucketMs * 1.5, 20 * 60 * 1000);
    const GAP_CAP = 0;
    const mapPoints = series.slice();
    if (mapPoints.length && zoomState.windowStart < mapPoints[0].t) {
      mapPoints.unshift({ t: zoomState.windowStart });
    }
    if (mapPoints.length && zoomState.windowEnd > mapPoints[mapPoints.length - 1].t) {
      mapPoints.push({ t: zoomState.windowEnd });
    }
    const tmap = this.buildTimeMap(mapPoints, GAP_THRESHOLD, GAP_CAP);
    const vWindowStart = tmap.toVirtual(zoomState.windowStart);
    const vWindowEnd = tmap.toVirtual(zoomState.windowEnd);
    const vSpan = Math.max(1, vWindowEnd - vWindowStart);

    const xScale = t => PAD.l + ((tmap.toVirtual(t) - vWindowStart) / vSpan) * PW;
    const yScale = v => PAD.t + PH - ((v - y0) / (y1 - y0)) * PH;

    // Store render context for interaction handlers
    this._lastRenderCtx = {
      windowStart: zoomState.windowStart,
      windowEnd: zoomState.windowEnd,
      span: zoomState.windowEnd - zoomState.windowStart,
      PAD, PW,
      dataMinT: dataBounds.minT,
      dataMaxT: dataBounds.maxT,
      minSpan: rangeConfig.bucketMs * 5,
      tmap, vWindowStart, vSpan,
      xScale, yScale
    };

    // Draw grid
    if (settings.showGrid) {
      this._drawGrid(ctx, PAD, PW, PH, W, y0, y1, C);
    }

    // Draw time labels
    this._drawTimeLabels(ctx, settings.range, zoomState, tmap, xScale, PAD, W, H, C);

    // Draw main chart (candles or line)
    if (settings.type === 'candle') {
      this._drawCandles(ctx, series, xScale, yScale, C, rangeConfig, vSpan, PW);
    } else {
      this._drawLine(ctx, series, xScale, yScale, C, settings.glow);
    }

    return { ctx, PAD, PW, PH, W, H, xScale, yScale, tmap, y0, y1, C };
  }

  _drawGrid(ctx, PAD, PW, PH, W, y0, y1, C) {
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    for (let g = 0; g <= 4; g++) {
      const gy = PAD.t + (g / 4) * PH;
      ctx.beginPath();
      ctx.moveTo(PAD.l, gy);
      ctx.lineTo(W - PAD.r, gy);
      ctx.stroke();
      const val = y1 - (g / 4) * (y1 - y0);
      ctx.fillStyle = C.axisLbl;
      ctx.font = '10px Inter,sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(fmtI(val), PAD.l - 6, gy + 3);
    }
  }

  _drawTimeLabels(ctx, range, zoomState, tmap, xScale, PAD, W, H, C) {
    const LABEL_STEP_MS = {
      '1m': 10 * 60 * 1000,
      '5m': 60 * 60 * 1000,
      '15m': 60 * 60 * 1000,
      '1h': 6 * 60 * 60 * 1000,
      '1d': 14 * 24 * 60 * 60 * 1000,
    };
    let labelStep = LABEL_STEP_MS[range];
    const span = zoomState.windowEnd - zoomState.windowStart;
    if (!labelStep) {
      const niceSteps = [1000,5000,10000,30000,60000,300000,600000,900000,1800000,3600000,7200000,14400000,21600000,43200000,86400000,7*86400000,14*86400000,30*86400000,60*86400000,90*86400000,180*86400000,365*86400000,730*86400000,1825*86400000];
      labelStep = niceSteps.find(s => s >= span/6) || niceSteps[niceSteps.length-1];
    }
    const showSeconds = labelStep < 60000;
    const showDate = labelStep >= 24 * 60 * 60 * 1000;
    const fmtOpts = showDate
      ? { month:'short', day:'numeric' }
      : showSeconds
        ? { hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false }
        : { hour:'2-digit', minute:'2-digit', hour12:false };
    ctx.font = '10px Inter,sans-serif';
    ctx.textAlign = 'center';
    for (let t = Math.ceil(zoomState.windowStart/labelStep)*labelStep; t <= zoomState.windowEnd; t += labelStep) {
      if (tmap.isInGap(t)) continue;
      const x = xScale(t);
      if (x < PAD.l - 1 || x > W - PAD.r + 1) continue;
      const label = showDate ? new Date(t).toLocaleDateString([], fmtOpts) : new Date(t).toLocaleTimeString([], fmtOpts);
      ctx.fillStyle = C.axisLbl;
      ctx.fillText(label, x, H - 6);
    }
    ctx.textAlign = 'left';
  }

  _drawCandles(ctx, series, xScale, yScale, C, rangeConfig, vSpan, PW) {
    const bucketPx = rangeConfig.bucketMs / vSpan * PW;
    const cw = Math.max(2, bucketPx * 0.6);
    series.forEach((c) => {
      const x = xScale(c.t);
      const up = c.c >= c.o;
      ctx.strokeStyle = ctx.fillStyle = up ? C.up : C.down;
      ctx.beginPath();
      ctx.moveTo(x, yScale(c.h));
      ctx.lineTo(x, yScale(c.l));
      ctx.stroke();
      const bodyTop = yScale(Math.max(c.o, c.c)), bodyBot = yScale(Math.min(c.o, c.c));
      ctx.fillRect(x - cw / 2, bodyTop, cw, Math.max(1, bodyBot - bodyTop));
    });
  }

  _drawLine(ctx, series, xScale, yScale, C, glow) {
    ctx.strokeStyle = C.line;
    ctx.lineWidth = 1.6;
    if (glow) {
      ctx.shadowColor = C.lineGlow;
      ctx.shadowBlur = 6;
    }
    ctx.beginPath();
    series.forEach((t, i) => {
      const x = xScale(t.t), y = yScale(t.p);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  drawOverlay(ctx, series, values, periods, color, xScale, yScale, calcFn) {
    periods.forEach(p => {
      const out = calcFn(values, p);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      let started = false;
      out.forEach((v, i) => {
        if (v == null) return;
        const x = xScale(series[i].t), y = yScale(v);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
  }

  drawVwap(ctx, series, xScale, yScale, color) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    let started = false;
    series.forEach((pt) => {
      if (pt.vw == null) return;
      const x = xScale(pt.t), y = yScale(pt.vw);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.restore();
  }

  getLastRenderCtx() {
    return this._lastRenderCtx;
  }
}
