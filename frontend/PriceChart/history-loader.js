// ============================================================
// history-loader.js
// Historical data loading component for price chart
// Handles fetching and caching OHLCV history from backend
// ============================================================

class HistoryLoader {
  constructor(chartData, onRenderRequest) {
    this.chartData = chartData;
    this.onRenderRequest = onRenderRequest;
    this._hydrateStarted = false;
  }

  // Fetch real OHLCV history for a specific range from backend
  async hydrateRange(range, force = false, symbol) {
    const sym = symbol || (typeof app !== 'undefined' && app.data.store.state && app.data.store.state.symbol) || 'default';
    
    if (!force && this.chartData.getHistoryBars(range, sym)) {
      this.onRenderRequest();
      return;
    }
    
    if (this.chartData.isHydrating(range, sym)) return;
    
    this.chartData.setHydrating(range, sym, true);
    
    try {
      const res = await fetch(`/api/history?symbol=${encodeURIComponent(sym)}&range=${encodeURIComponent(range)}`);
      if (!res.ok) {
        console.warn('[historyLoader] hydrateRange failed:', res.status, res.statusText, sym, range);
        return;
      }
      
      const rows = await res.json();
      if (!Array.isArray(rows)) return;
      
      const bars = rows
        .map(r => ({
          t: Number(r.t),
          o: parseFloat(r.o),
          h: parseFloat(r.h),
          l: parseFloat(r.l),
          c: parseFloat(r.c),
          v: (r.v != null && isFinite(r.v)) ? Number(r.v) : null,
        }))
        .filter(r => Number.isFinite(r.t) && Number.isFinite(r.o) && Number.isFinite(r.h)
                  && Number.isFinite(r.l) && Number.isFinite(r.c))
        .sort((a, b) => a.t - b.t);
      
      this.chartData.setHistoryBars(range, bars, sym);
      this.onRenderRequest();
    } catch (e) {
      console.warn('[historyLoader] hydrateRange error:', e);
    } finally {
      this.chartData.setHydrating(range, sym, false);
    }
  }

  // One-time backfill from backend's short-term tick history
  async hydrate(url) {
    if (this._hydrateStarted) return;
    this._hydrateStarted = true;
    
    try {
      const res = await fetch(url);
      if (!res.ok) {
        console.warn('[historyLoader] hydrate failed:', res.status, res.statusText, url);
        return;
      }
      
      const rows = await res.json();
      if (!Array.isArray(rows) || !rows.length) return;
      
      const hydrated = rows
        .map(r => ({
          t: Number(r.t),
          p: parseFloat(r.p),
          vw: (r.vw != null && isFinite(r.vw)) ? Number(r.vw) : null
        }))
        .filter(r => Number.isFinite(r.t) && Number.isFinite(r.p))
        .sort((a, b) => a.t - b.t);
      
      // Merge with existing ticks (keep live ticks that arrived during fetch)
      const existingTicks = this.chartData.ticks.slice();
      this.chartData.clear();
      hydrated.forEach(tick => this.chartData.addTick(tick.p, tick.t, tick.vw));
      existingTicks.forEach(tick => this.chartData.addTick(tick.p, tick.t, tick.vw));
      
      this.onRenderRequest();
    } catch (e) {
      console.warn('[historyLoader] hydrate error:', e);
    }
  }

  // Check if hydration has started
  hasHydrated() {
    return this._hydrateStarted;
  }

  reset() {
    this._hydrateStarted = false;
  }
}
