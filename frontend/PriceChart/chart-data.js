// ============================================================
// chart-data.js
// Data management component for price chart
// Handles tick storage, history bars, and data aggregation
// ============================================================

class ChartData {
  constructor(maxTicks = 50000) {
    this.ticks = []; // {t: epoch ms, p: price, vw: vwap}
    this.MAX_TICKS = maxTicks;
    this.MAX_TICK_AGE_MS = 7 * 24 * 60 * 60 * 1000; // Keep max 7 days of ticks
    this.historyBars = {}; // Cached OHLCV bars by symbol::range key
    this._hydratingRanges = new Set();
    this._currentSymbol = null;
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
    
    // Prune old ticks based on both count and age
    this._pruneTicks(now);
  }

  _pruneTicks(now = Date.now()) {
    // Remove ticks older than MAX_TICK_AGE_MS
    const cutoff = now - this.MAX_TICK_AGE_MS;
    const ageIdx = this.ticks.findIndex(tk => tk.t >= cutoff);
    if (ageIdx > 0) {
      this.ticks = this.ticks.slice(ageIdx);
    }
    
    // Also enforce max count limit
    if (this.ticks.length > this.MAX_TICKS) {
      this.ticks = this.ticks.slice(this.ticks.length - this.MAX_TICKS);
    }
  }

  // Cache key for historyBars - includes symbol to avoid cross-symbol contamination
  _histKey(range, symbol) {
    if (!symbol) throw new Error('ChartData: symbol is required (caller must supply it explicitly)');
    return `${symbol}::${range}`;
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

  // One-time backfill merge: replace current ticks with `hydratedTicks`
  // (from a history fetch), then replay `existingTicks` (whatever arrived
  // live while that fetch was in flight) back on top, both through
  // addTick() so its same-timestamp dedup/nudge logic applies to both
  // sets rather than being bypassed. Note this goes through clear(),
  // so — same as before this was encapsulated — historyBars and
  // hydrating-range state are wiped too, not just ticks.
  mergeHydratedTicks(hydratedTicks) {
    const existingTicks = this.ticks.slice();
    this.clear();
    hydratedTicks.forEach(tick => this.addTick(tick.p, tick.t, tick.vw));
    existingTicks.forEach(tick => this.addTick(tick.p, tick.t, tick.vw));
  }

  clear() {
    this.ticks = [];
    this.historyBars = {};
    this._hydratingRanges.clear();
    this._currentSymbol = null;
  }

  // Clear data when switching symbols to free memory
  clearForSymbolChange(newSymbol) {
    if (this._currentSymbol && this._currentSymbol !== newSymbol) {
      // Clear history bars for old symbol
      const oldPrefix = `${this._currentSymbol}::`;
      Object.keys(this.historyBars).forEach(key => {
        if (key.startsWith(oldPrefix)) {
          delete this.historyBars[key];
        }
      });
      // Clear ticks as they're for the old symbol
      this.ticks = [];
    }
    this._currentSymbol = newSymbol;
  }
}
