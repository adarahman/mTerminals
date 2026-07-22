// ============================================================
// indicator-engine.js
// Technical indicator calculations for price chart
// Handles SMA, EMA, and other indicator computations
// ============================================================

class IndicatorEngine {
  constructor() {
    this._cache = new Map(); // Cache for indicator calculations
    this._MAX_CACHE_SIZE = 50; // Limit cache entries to prevent unbounded growth
  }

  _getCacheKey(type, values, period, extra = '') {
    // Create a hash-like key from the data
    const len = values.length;
    const first = values[0]?.toFixed(2) || '0';
    const last = values[len - 1]?.toFixed(2) || '0';
    return `${type}:${period}:${len}:${first}:${last}:${extra}`;
  }

  _clearCache() {
    this._cache.clear();
  }

  _pruneCache() {
    if (this._cache.size > this._MAX_CACHE_SIZE) {
      // Remove oldest entries (first half)
      const entries = Array.from(this._cache.entries());
      this._cache.clear();
      entries.slice(Math.floor(entries.length / 2)).forEach(([k, v]) => this._cache.set(k, v));
    }
  }

  // Simple Moving Average
  sma(values, period) {
    const cacheKey = this._getCacheKey('sma', values, period);
    if (this._cache.has(cacheKey)) {
      return this._cache.get(cacheKey);
    }

    const out = new Array(values.length).fill(null);
    if (period <= 1 || values.length < period) return out;
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= period) sum -= values[i - period];
      if (i >= period - 1) out[i] = sum / period;
    }
    
    this._cache.set(cacheKey, out);
    this._pruneCache();
    return out;
  }

  // Exponential Moving Average
  ema(values, period) {
    const cacheKey = this._getCacheKey('ema', values, period);
    if (this._cache.has(cacheKey)) {
      return this._cache.get(cacheKey);
    }

    const out = new Array(values.length).fill(null);
    if (period <= 1 || values.length < period) return out;
    const k = 2 / (period + 1);
    let prev = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
    out[period - 1] = prev;
    for (let i = period; i < values.length; i++) {
      prev = values[i] * k + prev * (1 - k);
      out[i] = prev;
    }
    
    this._cache.set(cacheKey, out);
    this._pruneCache();
    return out;
  }

  // VWAP is provided directly from the data feed (Value/Volume from allIndices)
  // This method extracts it from the series for rendering
  extractVwap(series) {
    return series.map(pt => pt.vw);
  }

  // RSI (Relative Strength Index) - can be added if needed
  rsi(values, period = 14) {
    const out = new Array(values.length).fill(null);
    if (values.length < period * 2) return out;
    
    let gains = 0, losses = 0;
    for (let i = 1; i <= period; i++) {
      const change = values[i] - values[i - 1];
      if (change >= 0) gains += change;
      else losses -= change;
    }
    
    let avgGain = gains / period;
    let avgLoss = losses / period;
    
    out[period] = 100 - (100 / (1 + avgGain / avgLoss));
    
    for (let i = period + 1; i < values.length; i++) {
      const change = values[i] - values[i - 1];
      const gain = change >= 0 ? change : 0;
      const loss = change < 0 ? -change : 0;
      
      avgGain = ((avgGain * (period - 1)) + gain) / period;
      avgLoss = ((avgLoss * (period - 1)) + loss) / period;
      
      out[i] = 100 - (100 / (1 + avgGain / avgLoss));
    }
    
    return out;
  }

  // Bollinger Bands - can be added if needed
  bollingerBands(values, period = 20, multiplier = 2) {
    const middle = this.sma(values, period);
    const upper = new Array(values.length).fill(null);
    const lower = new Array(values.length).fill(null);
    
    for (let i = period - 1; i < values.length; i++) {
      const slice = values.slice(i - period + 1, i + 1);
      const std = Math.sqrt(slice.reduce((sum, v) => sum + Math.pow(v - middle[i], 2), 0) / period);
      upper[i] = middle[i] + multiplier * std;
      lower[i] = middle[i] - multiplier * std;
    }
    
    return { middle, upper, lower };
  }

  // MACD (Moving Average Convergence Divergence) - can be added if needed
  macd(values, fastPeriod = 12, slowPeriod = 26, signalPeriod = 9) {
    const emaFast = this.ema(values, fastPeriod);
    const emaSlow = this.ema(values, slowPeriod);
    const macdLine = new Array(values.length).fill(null);
    
    for (let i = slowPeriod - 1; i < values.length; i++) {
      macdLine[i] = emaFast[i] - emaSlow[i];
    }
    
    const signalLine = this.ema(macdLine.filter(v => v != null), signalPeriod);
    const histogram = new Array(values.length).fill(null);
    
    let signalIdx = 0;
    for (let i = 0; i < values.length; i++) {
      if (macdLine[i] != null && signalLine[signalIdx] != null) {
        histogram[i] = macdLine[i] - signalLine[signalIdx];
        signalIdx++;
      }
    }
    
    return { macdLine, signalLine, histogram };
  }
}
