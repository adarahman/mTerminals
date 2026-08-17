// ============================================================
// history-loader.js
// Historical data loading component for price chart
// Handles fetching and caching OHLCV history from backend
// ============================================================

// All chart surfaces share one browser-side request per symbol/range.
// Empty/error results are also cooled down so an upstream SmartAPI timeout
// cannot make every live render retry the same rate-limited endpoint.
const _marketHistoryRequests = new Map();
window.fetchMarketHistory = function(symbol, range, force = false, instrument = 'EQ', expiry = ''){
  const key = [String(symbol).toUpperCase(), instrument, expiry, range].join('|');
  const now = Date.now();
  const cached = _marketHistoryRequests.get(key);
  if(!force && cached && now - cached.startedAt < 60000) return cached.promise;
  const params = new URLSearchParams({symbol, range, instrument, expiry});
  const promise = fetch(`${Config.api.history}?${params.toString()}`)
    .then(res => res.ok ? res.json() : [])
    .then(rows => Array.isArray(rows) ? rows : [])
    .catch(() => []);
  _marketHistoryRequests.set(key, { startedAt: now, promise });
  return promise;
};

class HistoryLoader {
  constructor(chartData, onRenderRequest) {
    this.chartData = chartData;
    this.onRenderRequest = onRenderRequest;
    this._hydrateStarted = false;
  }

  // Fetch real OHLCV history for a specific range from backend
  async hydrateRange(range, force = false, symbol) {
    if (!symbol) throw new Error('HistoryLoader.hydrateRange: symbol is required');
    const sym = symbol;
    
    if (!force && this.chartData.getHistoryBars(range, sym)) {
      this.onRenderRequest();
      return;
    }
    
    if (this.chartData.isHydrating(range, sym)) return;
    
    this.chartData.setHydrating(range, sym, true);
    
    try {
      // The main chart is the cash/index analytical reference. Including
      // identity explicitly prevents future FUT views from sharing these
      // bars unless they also provide their exact contract expiry.
      const rows = await window.fetchMarketHistory(sym, range, force, 'EQ', '');
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
      Logger.warn('historyLoader', 'hydrateRange error:', e);
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
        Logger.warn('historyLoader', 'hydrate failed:', res.status, res.statusText, url);
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
      this.chartData.mergeHydratedTicks(hydrated);
      
      this.onRenderRequest();
    } catch (e) {
      Logger.warn('historyLoader', 'hydrate error:', e);
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
