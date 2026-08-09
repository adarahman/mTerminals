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
    // Heartbeat/keep-alive guard: some feeds keep pushing the last traded
    // price with a FRESH timestamp even when nothing has actually traded
    // (after close, weekends, holidays) — just to signal the connection
    // is alive. Only the timestamp used to differ in the old check below
    // (last.t === t), so those heartbeats each looked like a distinct new
    // tick and kept dragging the buffer's "last tick time" forward to
    // whatever moment the heartbeat arrived — which defeats anchoring the
    // chart's visible window on the last tick (see chart-data.js's
    // getVisibleTicks / price-chart-engine.js's render()): the window would keep
    // sliding to "now" even though the price hasn't genuinely moved since
    // the market closed. A tick with an unchanged price AND vwap is a
    // heartbeat, not new information — skip it regardless of its
    // timestamp, so the buffer's real last tick stays pinned to the last
    // actual price change (i.e. the real end of the last trading session).
    if (last && last.p === price && last.vw === vw) return;
    if (last && last.t === t) {
      // Genuine same-millisecond price move — nudge timestamp forward so
      // it still gets its own point instead of colliding with the last.
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
  //
  // Cutoff is anchored to the timestamp of the LAST TICK, not Date.now().
  // During live trading hours these are ~identical (ticks arrive in near
  // real time), so this changes nothing while the market's open. Outside
  // trading hours — after close, over a weekend, on a holiday — Date.now()
  // keeps advancing while the tick buffer doesn't, so an anchor on wall-
  // clock time would eventually push the cutoff past every tick from the
  // last session (e.g. checking 7 hours after a 3:30pm close with the 5m
  // range's 6.25hr window) and return an empty array — the chart would
  // show nothing instead of the last session. Anchoring to the last real
  // tick means "last N candles of actual trading," which is what a
  // holiday/after-hours view should show regardless of how much wall-clock
  // time has elapsed since then.
  getVisibleTicks(rangeConfig) {
    if (!rangeConfig || rangeConfig.ms === Infinity) return this.ticks;
    if (!this.ticks.length) return [];
    const lastT = this.ticks[this.ticks.length - 1].t;
    const cutoff = lastT - rangeConfig.ms;
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