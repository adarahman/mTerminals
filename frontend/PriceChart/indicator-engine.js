// ============================================================
// indicator-engine.js
// Technical indicator calculations for price chart
// Handles SMA, EMA, and other indicator computations
// ============================================================

class IndicatorEngine {
  constructor() {}

  // Simple Moving Average
  sma(values, period) {
    const out = new Array(values.length).fill(null);
    if (period <= 1 || values.length < period) return out;
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= period) sum -= values[i - period];
      if (i >= period - 1) out[i] = sum / period;
    }
    return out;
  }

  // Exponential Moving Average
  ema(values, period) {
    const out = new Array(values.length).fill(null);
    if (period <= 1 || values.length < period) return out;
    const k = 2 / (period + 1);
    let prev = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
    out[period - 1] = prev;
    for (let i = period; i < values.length; i++) {
      prev = values[i] * k + prev * (1 - k);
      out[i] = prev;
    }
    return out;
  }

}
