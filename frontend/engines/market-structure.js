// ============================================================
// shared/market-structure.js
// Shared Market Structure classifier
// ============================================================

(function () {

  function median(values) {
    const s = values.slice().sort((a, b) => a - b);
    return s.length ? s[Math.floor(s.length / 2)] : 0;
  }

  // ATM ± this many strike steps = "near". Shared so the Strike Detail
  // table, the Vol/OI Velocity bars, and the Institutional Activity Crux
  // card can never drift out of sync on where "near" ends — previously
  // duplicated verbatim in panels-views.js and OptionChain/option-chain.js.
  const INST_NEAR_BAND_STRIKES = 10;

  function instBandFor(strike, atm, step) {
    const s = step > 0 ? step : 50;
    const stepIdx = Math.round(Math.abs(strike - atm) / s);
    return stepIdx <= INST_NEAR_BAND_STRIKES ? "near" : "far";
  }

  window.median = median;
  window.INST_NEAR_BAND_STRIKES = INST_NEAR_BAND_STRIKES;
  window.instBandFor = instBandFor;

  const MARKET_STRUCTURE = {
    MAJOR_MULT: 1.30,
    WEAK_MULT: 0.80,
    FRESH_CHG_MULT: 0.50
  };

  function marketStructureLabels(rows, atm, oiByStrike, maxPainStrike) {

    const labels = {};

    const resPool = rows
      .filter(r => r.strike >= atm)
      .map(r => {
        const s = oiByStrike[r.strike] || {};
        return {
          strike: r.strike,
          oi: s.ce || 0,
          chg: s.ceChg || 0
        };
      })
      .sort((a, b) => b.oi - a.oi);

    const supPool = rows
      .filter(r => r.strike <= atm)
      .map(r => {
        const s = oiByStrike[r.strike] || {};
        return {
          strike: r.strike,
          oi: s.pe || 0,
          chg: s.peChg || 0
        };
      })
      .sort((a, b) => b.oi - a.oi);

    const resMedian = median(resPool.map(x => x.oi));
    const supMedian = median(supPool.map(x => x.oi));

    resPool.forEach((p, i) => {

      if (p.strike === maxPainStrike) {
        labels[p.strike] = {
          text: "Max Pain",
          color: "#a855f7"
        };
        return;
      }

      if (p.oi <= 0) return;

      if (i === 0) {
        labels[p.strike] = {
          text: "★ Major Resistance",
          color: "#dc2626"
        };
      }
      else if (
        i === 1 &&
        p.oi > resMedian * MARKET_STRUCTURE.MAJOR_MULT
      ) {
        labels[p.strike] = {
          text: "Resistance Building",
          color: "var(--red)"
        };
      }
      else if (
        p.chg > p.oi * MARKET_STRUCTURE.FRESH_CHG_MULT &&
        p.oi < resMedian * MARKET_STRUCTURE.MAJOR_MULT
      ) {
        labels[p.strike] = {
          text: "Fresh Writing",
          color: "var(--amber)"
        };
      }
      else if (
        p.oi > resMedian * MARKET_STRUCTURE.WEAK_MULT
      ) {
        labels[p.strike] = {
          text: "Weak Resistance",
          color: "var(--txt3)"
        };
      }

    });

    supPool.forEach((p, i) => {

      if (labels[p.strike]) return;

      if (p.oi <= 0) return;

      if (i === 0) {
        labels[p.strike] = {
          text: "★ Major Support",
          color: "#10b981"
        };
      }
      else if (
        i === 1 &&
        p.oi > supMedian * MARKET_STRUCTURE.MAJOR_MULT
      ) {
        labels[p.strike] = {
          text: "Support Building",
          color: "var(--green)"
        };
      }
      else if (
        p.chg > p.oi * MARKET_STRUCTURE.FRESH_CHG_MULT &&
        p.oi < supMedian * MARKET_STRUCTURE.MAJOR_MULT
      ) {
        labels[p.strike] = {
          text: "PE Writing",
          color: "var(--amber)"
        };
      }
      else if (
        p.oi > supMedian * MARKET_STRUCTURE.WEAK_MULT
      ) {
        labels[p.strike] = {
          text: "Weak Support",
          color: "var(--txt3)"
        };
      }

    });

    return labels;
  }

  window.marketStructureLabels = marketStructureLabels;

})();