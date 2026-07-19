// ============================================================
// chart-data.js
// Data management component for price chart
// Handles tick storage, history bars, and data aggregation
// ============================================================

class ChartData {
  constructor(maxTicks = 50000) {
    this.ticks = []; // {t: epoch ms, p: price, vw: vwap}
    this.MAX_TICKS = maxTicks;
    this.historyBars = {}; // Cached OHLCV bars by symbol::range key
    this._hydratingRanges = new Set();
  }

  addTick(price, t, vwap) {
    price = parseFloat(price);
    if (!price || isNaN(price)) return;
    const now = Date.now();
    t = t || now;
    const vw = (vwap != null && isFinite(vwap)) ? vwap : null;
    const last = this.ticks[this.ticks.length - 1];
    if (last && last.t === t) {
      if (last.p === price && last.vw === vw) return; // true no-op
      // Nudge timestamp forward for same-timestamp price moves
      t = Math.max(now, last.t + 1);
    }
    this.ticks.push({ t, p: price, vw });
    if (this.ticks.length > this.MAX_TICKS) this.ticks.shift();
  }

  // Cache key for historyBars - includes symbol to avoid cross-symbol contamination
  _histKey(range, symbol) {
    const sym = symbol || (typeof app !== 'undefined' && app.data.store.state && app.data.store.state.symbol) || 'default';
    return `${sym}::${range}`;
  }

  setHistoryBars(range, bars, symbol) {
    const key = this._histKey(range, symbol);
    this.historyBars[key] = bars;
  }

  getHistoryBars(range, symbol) {
    return this.historyBars[this._histKey(range, symbol)];
  }

  isHydrating(range, symbol) {
    return this._hydratingRanges.has(this._histKey(range, symbol));
  }

  setHydrating(range, symbol, isHydrating) {
    const key = this._histKey(range, symbol);
    if (isHydrating) {
      this._hydratingRanges.add(key);
    } else {
      this._hydratingRanges.delete(key);
    }
  }

  // Filter ticks to visible window based on range config
  getVisibleTicks(rangeConfig) {
    if (!rangeConfig || rangeConfig.ms === Infinity) return this.ticks;
    const cutoff = Date.now() - rangeConfig.ms;
    const idx = this.ticks.findIndex(tk => tk.t >= cutoff);
    return idx < 0 ? [] : this.ticks.slice(idx);
  }

  // Aggregate ticks into OHLCV candles
  aggregateCandles(ticks, bucketMs) {
    if (!ticks.length) return [];
    const candles = [];
    let cur = null;
    for (const tk of ticks) {
      const bucketStart = Math.floor(tk.t / bucketMs) * bucketMs;
      if (!cur || cur.t !== bucketStart) {
        if (cur) candles.push(cur);
        cur = { t: bucketStart, o: tk.p, h: tk.p, l: tk.p, c: tk.p, vw: tk.vw };
      } else {
        cur.h = Math.max(cur.h, tk.p);
        cur.l = Math.min(cur.l, tk.p);
        cur.c = tk.p;
        if (tk.vw != null) cur.vw = tk.vw;
      }
    }
    if (cur) candles.push(cur);
    return candles;
  }

  // Merge historical bars with live ticks for real-time updates
  mergeLiveBars(bars, visibleTicks, bucketMs, windowStart, windowEnd) {
    if (!bars || !bars.length) return null;
    const lastBarT = bars[bars.length - 1].t;
    const liveTail = visibleTicks.filter(tk => tk.t > lastBarT);
    const tailCandles = liveTail.length ? this.aggregateCandles(liveTail, bucketMs) : [];
    return bars.concat(tailCandles).filter(c => c.t >= windowStart && c.t <= windowEnd);
  }

  getLastTick() {
    return this.ticks[this.ticks.length - 1];
  }

  clear() {
    this.ticks = [];
    this.historyBars = {};
    this._hydratingRanges.clear();
  }
}
