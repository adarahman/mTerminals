/* ════════════════════════════════════════════════════════════════════
   OPTION CHAIN — standalone page logic

   Row shape consumed here is deliberately identical to what
   ChainDenseView.mapPayloadToRows() in chain-views.js already produces:

     { strike, isAtm, pcr, pcrChg,
       ce: { iv, ivChg, vol, ltp, chg, oi, oiChg, oiVel, velTrend, signal },
       pe: { iv, ivChg, vol, ltp, chg, oi, oiChg, oiVel, velTrend, signal },
       totalCeOi, totalPeOi }

   That means this page can be wired to the live dashboard with almost no
   translation layer. Two ways to feed it real data:

   1. BroadcastChannel (recommended — keeps this as its own tab/window,
      updates live, no polling). On the MAIN dashboard, ChainDenseView
      (chain-sync.js) already opens a BroadcastChannel('oc-live-sync')
      in its constructor and posts the latest rows to it (via its own
      this._ocChan instance property, not a window global) every time
      refreshView() runs. That's the only wiring needed on the dashboard
      side — this file already listens for it below.

   2. window.postMessage from an opener window, if you'd rather open this
      as a child tab via window.open() than a broadcast — see
      window.opener handling below, same message shape.

   With neither connected, this file falls back to DEMO DATA so the page
   is fully viewable/clickable on its own.
   ════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ── STATE ──
  let state = {
    symbol: "NIFTY",
    spot: 24062.40,
    spotChg: 118.30,
    spotChgPct: 0.49,
    expiry: "24-JUL-2026",
    expiryDates: ["24-JUL-2026", "31-JUL-2026", "07-AUG-2026"],
    rows: [],
    range: 10,
    velWin: 5,
    greeksOpen: false,
    selectedStrike: null,
  };
  // Set by initLiveSync() when a BroadcastChannel to the dashboard tab is
  // open; used by the expiry dropdown's change handler to ask the
  // dashboard to drive the real expiry switch. Module state (closure
  // variable), not window._ocRequestExpiry — nothing outside this IIFE
  // ever needs to read it.
  let _ocRequestExpiry = null;

  // Figures below are RAW absolute numbers (contracts / shares) — the
  // same units chain-views.js's mapPayloadToRows() produces for ce.oi,
  // ce.vol, ce.oiChg etc. They are NOT pre-scaled to K/L; fmt() above
  // does that scaling at render time, same as it will for live data.
  function buildDemoRows() {
    const raw = [
      { strike: 23900, ceLTP: 248.80, ceChg: 2.90, ceChgPct: 1.18, ceIV: 13.38, ceVol: 8175000, ceOI: 613000, ceOIchg: 15300, ceVel: [4600, 9200, 15300], ceSig: "bullish",
        ceBid: 248.60, ceBidQty: 1200, ceAsk: 249.05, ceAskQty: 950, ceTotBidQty: 42500, ceTotAskQty: 31800, ceDelta: 0.62, ceGamma: 0.0018, ceTheta: -6.4, ceVega: 9.8,
        peLTP: 59.55, peChg: -33.05, peChgPct: -35.7, peIV: 13.04, peVol: 62987000, peOI: 3732000, peOIchg: 1407000, peVel: [420000, 840000, 1407000], peSig: "bullish", pcr: 6.09, pcrChg: 2.20,
        peBid: 59.30, peBidQty: 3100, peAsk: 59.80, peAskQty: 2650, peTotBidQty: 118000, peTotAskQty: 96500, peDelta: -0.38, peGamma: 0.0018, peTheta: -5.1, peVega: 9.6 },
      { strike: 23950, ceLTP: 212.00, ceChg: -1.90, ceChgPct: -0.89, ceIV: 13.26, ceVol: 7523000, ceOI: 376000, ceOIchg: 91800, ceVel: [27000, 55000, 91800], ceSig: "mixed",
        ceBid: 211.75, ceBidQty: 900, ceAsk: 212.30, ceAskQty: 1100, ceTotBidQty: 28900, ceTotAskQty: 33400, ceDelta: 0.57, ceGamma: 0.0019, ceTheta: -6.7, ceVega: 10.1,
        peLTP: 74.15, peChg: -36.30, peChgPct: -32.9, peIV: 12.91, peVol: 36158000, peOI: 1760000, peOIchg: 540000, peVel: [160000, 320000, 540000], peSig: "mixed", pcr: 4.69, pcrChg: 0.39,
        peBid: 73.85, peBidQty: 2400, peAsk: 74.40, peAskQty: 2100, peTotBidQty: 71200, peTotAskQty: 65800, peDelta: -0.43, peGamma: 0.0019, peTheta: -5.4, peVega: 9.9 },
      { strike: 24000, ceLTP: 178.80, ceChg: -4.70, ceChgPct: null, ceIV: 13.15, ceVol: 48631000, ceOI: 3874000, ceOIchg: -712000, ceVel: [-210000, -430000, -712000], ceSig: "strong-bullish",
        ceBid: 178.55, ceBidQty: 3400, ceAsk: 179.10, ceAskQty: 2900, ceTotBidQty: 145000, ceTotAskQty: 118000, ceDelta: 0.52, ceGamma: 0.0021, ceTheta: -7.0, ceVega: 10.4,
        peLTP: 91.35, peChg: -39.35, peChgPct: -30.1, peIV: 12.77, peVol: 114610000, peOI: 6476000, peOIchg: 540000, peVel: [160000, 320000, 540000], peSig: "strong-bullish", pcr: 1.67, pcrChg: 0.38,
        peBid: 91.05, peBidQty: 4600, peAsk: 91.60, peAskQty: 4100, peTotBidQty: 189000, peTotAskQty: 172000, peDelta: -0.48, peGamma: 0.0021, peTheta: -5.6, peVega: 10.2 },
      { strike: 24050, ceLTP: 149.90, ceChg: -6.60, ceChgPct: -4.22, ceIV: 13.02, ceVol: 45125000, ceOI: 500000, ceOIchg: 63600, ceVel: [19000, 38000, 63600], ceSig: "mixed", isAtm: true,
        ceBid: 149.65, ceBidQty: 2900, ceAsk: 150.20, ceAskQty: 2500, ceTotBidQty: 98000, ceTotAskQty: 91000, ceDelta: 0.50, ceGamma: 0.0022, ceTheta: -7.2, ceVega: 10.6,
        peLTP: 111.25, peChg: -42.25, peChgPct: -27.5, peIV: 12.77, peVol: 71453000, peOI: 3298000, peOIchg: 1450000, peVel: [430000, 870000, 1450000], peSig: "mixed", pcr: 1.45, pcrChg: 0.62,
        peBid: 110.95, peBidQty: 3800, peAsk: 111.55, peAskQty: 3300, peTotBidQty: 132000, peTotAskQty: 121000, peDelta: -0.50, peGamma: 0.0022, peTheta: -5.8, peVega: 10.5 },
      { strike: 24100, ceLTP: 122.35, ceChg: -9.65, ceChgPct: -7.31, ceIV: 12.96, ceVol: 154446000, ceOI: 7638000, ceOIchg: 819000, ceVel: [250000, 500000, 819000], ceSig: "mixed",
        ceBid: 122.10, ceBidQty: 5200, ceAsk: 122.65, ceAskQty: 4700, ceTotBidQty: 214000, ceTotAskQty: 198000, ceDelta: 0.47, ceGamma: 0.0021, ceTheta: -7.0, ceVega: 10.4,
        peLTP: 134.75, peChg: -44.15, peChgPct: -24.7, peIV: 12.60, peVol: 167903000, peOI: 6083000, peOIchg: 1002000, peVel: [300000, 600000, 1002000], peSig: "mixed", pcr: 0.80, pcrChg: 0.05,
        peBid: 134.40, peBidQty: 4900, peAsk: 135.05, peAskQty: 4400, peTotBidQty: 176000, peTotAskQty: 163000, peDelta: -0.53, peGamma: 0.0021, peTheta: -5.5, peVega: 10.3 },
    ];
    return raw.map((r) => ({
      strike: r.strike,
      isAtm: !!r.isAtm,
      pcr: r.pcr.toFixed(2),
      pcrChg: (r.pcrChg >= 0 ? "+" : "") + r.pcrChg.toFixed(2),
      ce: {
        iv: r.ceIV, ivChg: r.ceIVchg, vol: r.ceVol, ltp: r.ceLTP, chg: r.ceChg, chgPct: r.ceChgPct,
        oi: r.ceOI, oiChg: r.ceOIchg, velTrend: r.ceVel, signal: r.ceSig,
        bid: r.ceBid, bidQty: r.ceBidQty, ask: r.ceAsk, askQty: r.ceAskQty,
        totalBidQty: r.ceTotBidQty, totalAskQty: r.ceTotAskQty,
        delta: r.ceDelta, gamma: r.ceGamma, theta: r.ceTheta, vega: r.ceVega,
      },
      pe: {
        iv: r.peIV, ivChg: r.peIVchg, vol: r.peVol, ltp: r.peLTP, chg: r.peChg, chgPct: r.peChgPct,
        oi: r.peOI, oiChg: r.peOIchg, velTrend: r.peVel, signal: r.peSig,
        bid: r.peBid, bidQty: r.peBidQty, ask: r.peAsk, askQty: r.peAskQty,
        totalBidQty: r.peTotBidQty, totalAskQty: r.peTotAskQty,
        delta: r.peDelta, gamma: r.peGamma, theta: r.peTheta, vega: r.peVega,
      },
    }));
  }

  // ── EXPIRY DATE SORT ──
  // Expiry strings are "DD-MMM-YYYY" (e.g. "07-AUG-2026"), which do NOT
  // sort correctly as plain strings — comparing them lexicographically
  // puts "07-AUG-2026" before "24-JUL-2026" because it compares the day
  // digit first, scrambling the dropdown across month boundaries whenever
  // the incoming expiryDates array isn't already in calendar order. This
  // is the same logic as sortExpiryDates()/parseExpiryDate() in
  // dashboard.js, duplicated here because this page runs standalone and
  // doesn't load that script.
  const EXPIRY_MONTHS = { JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5, JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11 };
  const parseExpiryDate = (str) => {
    if (!str) return Infinity;
    const m = /^(\d{1,2})-([A-Za-z]{3})-(\d{4})$/.exec(String(str).trim());
    if (m) {
      const mon = EXPIRY_MONTHS[m[2].toUpperCase()];
      if (mon != null) return new Date(+m[3], mon, +m[1]).getTime();
    }
    const parsed = Date.parse(str);
    return isNaN(parsed) ? Infinity : parsed;
  };
  const sortExpiryDates = (dates) => (Array.isArray(dates) ? dates.slice().sort((a, b) => parseExpiryDate(a) - parseExpiryDate(b)) : dates);

  // ── FORMATTERS ──
  // Single source of truth for K/L/Cr formatting. Takes the RAW absolute
  // number (e.g. 613000 contracts, not "6.13"), same shape chain-views.js's
  // mapPayloadToRows() sends over BroadcastChannel — the old fmtL/fmtK
  // helpers here assumed the caller had already pre-scaled the value,
  // which silently produced the wrong unit (and wrong magnitude) any time
  // the true figure didn't happen to fall in the range each helper guessed
  // for. This is unit-aware from the raw number itself, so it can't drift
  // from what's actually being displayed.
  const fmt = (v) => {
    if (v == null || isNaN(v)) return "—";
    const a = Math.abs(v);
    const s = v < 0 ? "-" : "";
    if (a >= 1e7) return s + (a / 1e7).toFixed(2) + "Cr";
    if (a >= 1e5) return s + (a / 1e5).toFixed(2) + "L";
    if (a >= 1e3) return s + (a / 1e3).toFixed(1) + "K";
    return s + a.toFixed(0);
  };
  const fmtNum = (v, d = 2) => (v == null ? "—" : v.toFixed(d));
  // LTP change readout — %chg is optional data (not every feed/leg has a
  // prior-close reference to compute it from). Showing "12.4 ()" when
  // it's missing is worse than just showing "12.4"; only append the
  // percentage when it actually exists.
  const ltpChgStr = (chg, chgPct) => {
    if (chg == null) return "—";
    const base = `${sign(chg)}${fmtNum(chg)}`;
    return chgPct == null ? base : `${base} (${sign(chgPct)}${fmtNum(chgPct, 1)}%)`;
  };
  const sign = (v) => (v > 0 ? "+" : "");
  const signClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
  // Single OI color rule, used everywhere OI values/bars are shown:
  // CE >= 0 -> red, CE < 0 -> green. PE >= 0 -> green, PE < 0 -> red.
  const ceOiCls = (v) => (v || 0) >= 0 ? "chg-red" : "chg-green";
  const peOiCls = (v) => (v || 0) >= 0 ? "chg-green" : "chg-red";

  // Strike-level CE-vs-PE directional divergence — CE rising while PE
  // falling (calls being bid up, puts being sold off) reads "div-red";
  // the mirror case (CE falling, PE rising) reads "div-green". Same-
  // direction moves (both up, both down) or missing data aren't a
  // divergence, so no dot is shown — this is a distinct signal from
  // signClass above, which only looks at one leg at a time.
  const ceVsPeDivergence = (ceChg, peChg) => {
    if (ceChg == null || peChg == null) return null;
    if (ceChg > 0 && peChg < 0) return "div-red";
    if (ceChg < 0 && peChg > 0) return "div-green";
    return null;
  };

  const SIGNAL_LABEL = {
    "bullish": "Bullish", "strong-bullish": "Strong Bullish",
    "bearish": "Bearish", "strong-bearish": "Strong Bearish",
    "mixed": "Mixed", "neutral": "Neutral",
  };
  const badge = (sig) => {
    const key = sig || "neutral";
    return `<span class="oc-badge oc-badge-${key}">${SIGNAL_LABEL[key] || "Neutral"}</span>`;
  };

  // Composite signal — the rightmost column is a read on the STRIKE, not
  // on one leg, so it has to actually combine CE + PE rather than just
  // echoing whichever leg happens to match. Each leg is scored on a
  // -2..+2 bullish/bearish scale, then averaged; legs that openly
  // disagree (one bullish, one bearish) collapse to "mixed" since that's
  // a genuine conflict, not an in-between reading.
  const SIGNAL_RANK = {
    "strong-bearish": -2, "bearish": -1, "neutral": 0, "mixed": 0,
    "bullish": 1, "strong-bullish": 2,
  };
  function compositeSignal(ceSig, peSig) {
    const c = SIGNAL_RANK[ceSig] ?? 0;
    const p = SIGNAL_RANK[peSig] ?? 0;
    if ((c > 0 && p < 0) || (c < 0 && p > 0)) return "mixed";
    const avg = (c + p) / 2;
    if (avg >= 1.5) return "strong-bullish";
    if (avg >= 0.5) return "bullish";
    if (avg <= -1.5) return "strong-bearish";
    if (avg <= -0.5) return "bearish";
    return "neutral";
  }

  // tri-bar sparkline for OI velocity across 5/15/30m
  function velBars(trend) {
    if (!trend) return `<div class="oc-vel-bars"><i></i><i></i><i></i></div>`;
    const max = Math.max(1, ...trend.map((v) => Math.abs(v || 0)));
    return `<div class="oc-vel-bars">${trend.map((v) => {
      const h = Math.max(2, Math.round((Math.abs(v || 0) / max) * 16));
      const dir = v > 0 ? "up" : v < 0 ? "down" : "";
      return `<i class="${dir}" style="height:${h}px;"></i>`;
    }).join("")}</div>`;
  }

  // ── OI BAR CHANGE INDICATOR ──
  // Single-bar OI visualization: the bar's total length always tracks
  // CURRENT OI only (never shifts because of a change), and the intraday
  // change is drawn as a subtle overlay INSIDE that same bar rather than
  // as a second competing bar:
  //   oiChg > 0 (fresh buildup)  -> dashed/hatched overlay
  //   oiChg < 0 (unwind/cover)   -> hollow/cut-out-look overlay
  // Overlay width = |oiChg| / current OI, i.e. the fraction of the
  // CURRENT bar's own length that the change represents — so it's always
  // readable relative to the bar it's sitting inside, same rule for CE
  // and PE. A 2–3px floor keeps tiny changes visible without needing to
  // round up to a misleading percentage.
  function oiChangeIndicator(oi, oiChg) {
    if (!oiChg) return "";
    const ratio = Math.min(1, Math.abs(oiChg) / Math.max(oi || 0, 1));
    const pct = (ratio * 100).toFixed(1);
    const added = oiChg > 0;
    const type = added ? "oi-added" : "oi-reduced";
    const label = added ? "Fresh OI added" : "OI unwound";
    return `<div class="oc-oi-bar-indicator ${type}" style="width:max(3px, ${pct}%);" title="${label}: ${sign(oiChg)}${fmt(oiChg)} (${pct}% of current OI)"></div>`;
  }

  // ── ROW RENDER ──
  function buildRowHtml(r) {
    const maxOi = Math.max(r.ce.oi, r.pe.oi, 1);
    const cePct = Math.min(100, (r.ce.oi / maxOi) * 100);
    const pePct = Math.min(100, (r.pe.oi / maxOi) * 100);
    const gaugeCe = Math.min(100, (r.ce.oi / (r.ce.oi + r.pe.oi || 1)) * 100);
    const gaugePe = 100 - gaugeCe;
    const maxChg = Math.max(Math.abs(r.ce.oiChg || 0), Math.abs(r.pe.oiChg || 0), 1);
    const ceChgPct = Math.min(100, (Math.abs(r.ce.oiChg || 0) / maxChg) * 100);
    const peChgPct = Math.min(100, (Math.abs(r.pe.oiChg || 0) / maxChg) * 100);
    const ceOiValCls = ceOiCls(r.ce.oi);
    const peOiValCls = peOiCls(r.pe.oi);
    const ceChgCls = ceOiCls(r.ce.oiChg);
    const peChgCls = peOiCls(r.pe.oiChg);

    const rowHtml = `
    <tr class="oc-row${r.isAtm ? " oc-atm" : ""}" data-strike="${r.strike}">
      <td class="oc-iv-cell">
        <div class="oc-stack">
          <span class="pe">${fmtNum(r.pe.iv)}%</span>
          <span class="ce">${fmtNum(r.ce.iv)}%</span>
        </div>
      </td>
      <td class="oc-vol-cell">
        <div class="oc-stack">
          <span class="pe">${fmt(r.pe.vol)}</span>
          <span class="ce">${fmt(r.ce.vol)}</span>
        </div>
      </td>
      <td class="oc-ltp-cell" onclick="event.stopPropagation();window.ocOpenTradeModal(${r.strike},'CE',${r.ce.ltp != null ? r.ce.ltp : "null"})" title="Click to trade this strike">
        <span class="oc-ltp-main oc-call-c">${fmtNum(r.ce.ltp)}</span>
        <span class="oc-ltp-sub ${signClass(r.ce.chg)}">${ltpChgStr(r.ce.chg, r.ce.chgPct)}</span>
      </td>
      <td class="oc-ltp-cell oc-ltp-adjacent" onclick="event.stopPropagation();window.ocOpenTradeModal(${r.strike},'PE',${r.pe.ltp != null ? r.pe.ltp : "null"})" title="Click to trade this strike">
        <span class="oc-ltp-main oc-put-c">${fmtNum(r.pe.ltp)}</span>
        <span class="oc-ltp-sub ${signClass(r.pe.chg)}">${ltpChgStr(r.pe.chg, r.pe.chgPct)}</span>
      </td>
      <td class="oc-strike-cell" onclick="event.stopPropagation();window.ocOpenDepth(${r.strike})" title="Click for Bid/Ask depth">
        <span class="oc-strike-val">${r.strike}${ceVsPeDivergence(r.ce.chg, r.pe.chg) ? `<i class="oc-strike-div ${ceVsPeDivergence(r.ce.chg, r.pe.chg)}" title="${ceVsPeDivergence(r.ce.chg, r.pe.chg) === 'div-red' ? 'CE up / PE down' : 'CE down / PE up'}"></i>` : ""}</span>
        <div class="oc-strike-gauge">
          <div class="oc-strike-gauge-pe" style="width:${gaugePe}%;"></div>
          <div class="oc-strike-gauge-ce" style="width:${gaugeCe}%;"></div>
        </div>
        <span class="oc-strike-pcr">PCR <b>${r.pcr}</b> <span class="${signClass(parseFloat(r.pcrChg))}">${r.pcrChg}</span></span>
      </td>
      <td class="oc-oi-cell">
        <div class="oc-oi-row"><span class="oc-oi-val ${peOiValCls}">${fmt(r.pe.oi)}</span>
          <div class="oc-oi-bar-track"><div class="oc-oi-bar-fill ${peOiValCls}" style="width:${pePct}%;">${oiChangeIndicator(r.pe.oi, r.pe.oiChg)}</div></div></div>
        <div class="oc-oi-row"><span class="oc-oi-val ${ceOiValCls}">${fmt(r.ce.oi)}</span>
          <div class="oc-oi-bar-track"><div class="oc-oi-bar-fill ${ceOiValCls}" style="width:${cePct}%;">${oiChangeIndicator(r.ce.oi, r.ce.oiChg)}</div></div></div>
      </td>
      <td class="oc-chg-cell">
        <div class="oc-chg-row"><span class="oc-chg-val ${peChgCls}">${sign(r.pe.oiChg)}${fmt(r.pe.oiChg)}</span>
          <div class="oc-chg-bar-track"><div class="oc-chg-bar-fill ${peChgCls}" style="width:${peChgPct}%;"></div></div></div>
        <div class="oc-chg-row"><span class="oc-chg-val ${ceChgCls}">${sign(r.ce.oiChg)}${fmt(r.ce.oiChg)}</span>
          <div class="oc-chg-bar-track"><div class="oc-chg-bar-fill ${ceChgCls}" style="width:${ceChgPct}%;"></div></div></div>
      </td>
      <td class="oc-vel-cell">
        <div class="oc-vel-row"><span class="oc-vel-num pe">PE</span>${velBars(r.pe.velTrend)}</div>
        <div class="oc-vel-row"><span class="oc-vel-num ce">CE</span>${velBars(r.ce.velTrend)}</div>
      </td>
      <td class="oc-sig-cell">${badge(compositeSignal(r.ce.signal, r.pe.signal))}</td>
    </tr>`;

    return rowHtml;
  }

  // Greeks row — a second, visually distinct <tr> under the strike row.
  // It is only ever added/removed by renderRows() reading state.greeksOpen,
  // which the Greeks button is the sole owner of, so nothing else in the
  // page (row clicks, strike clicks, live ticks) can silently close it.
  function buildGreekRowHtml(r) {
    const g = (leg) => `
      <div class="oc-greek-item"><span>Δ</span> ${fmtNum(leg.delta, 3)}</div>
      <div class="oc-greek-item"><span>Γ</span> ${fmtNum(leg.gamma, 4)}</div>
      <div class="oc-greek-item"><span>Θ</span> ${fmtNum(leg.theta, 2)}</div>
      <div class="oc-greek-item"><span>Vega</span> ${fmtNum(leg.vega, 2)}</div>`;
    return `
    <tr class="oc-greek-row" data-strike="${r.strike}">
      <td colspan="9">
        <div class="oc-greek-wrap">
          <div class="oc-greek-side pe"><b>PE</b>${g(r.pe)}</div>
          <div class="oc-greek-side ce"><b>CE</b>${g(r.ce)}</div>
        </div>
      </td>
    </tr>`;
  }

  function visibleRows() {
    if (state.range >= 9999) return state.rows;
    const atmIdx = state.rows.findIndex((r) => r.isAtm);
    if (atmIdx === -1) return state.rows;
    return state.rows.slice(Math.max(0, atmIdx - state.range), atmIdx + state.range + 1);
  }

  // ── HEADER / SKEW RENDER ──
  function renderHeader() {
    $("ocSpot").textContent = state.spot.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const chgEl = $("ocSpotChg");
    const up = state.spotChg >= 0;
    chgEl.textContent = `${up ? "+" : ""}${state.spotChg.toFixed(2)} (${up ? "+" : ""}${state.spotChgPct.toFixed(2)}%)`;
    chgEl.className = "oc-spot-chg" + (up ? "" : " down");
    document.querySelector("#ocSymbol").childNodes[0].nodeValue = state.symbol + " ";

    const sel = $("ocExpiry");
    const sortedExpiryDates = sortExpiryDates(state.expiryDates);
    if (sel.dataset.key !== sortedExpiryDates.join("|")) {
      sel.innerHTML = sortedExpiryDates.map((d) => `<option value="${d}"${d === state.expiry ? " selected" : ""}>${d}</option>`).join("");
      sel.dataset.key = sortedExpiryDates.join("|");
    }
  }

  const VEL_WINDOWS = [5, 15, 30];

  function renderSummary() {
    const rows = visibleRows();

    // ── RANGE BADGE ── every summary card was aggregating over
    // visibleRows() (ATM ± state.range) with no on-card indication of
    // what that range actually was — a ±3 read and a ±10 read look
    // identical at a glance even though the totals mean very different
    // things. Stamp the current range onto every card via a shared class
    // instead of hand-wiring four separate ids.
    const rangeLabel = state.range >= 9999 ? "All strikes" : `±${state.range}`;
    document.querySelectorAll(".oc-range-badge").forEach((el) => { el.textContent = rangeLabel; });

    // ── OI summary ──
    const totalCe = rows.reduce((s, r) => s + r.ce.oi, 0);
    const totalPe = rows.reduce((s, r) => s + r.pe.oi, 0);
    const oiTotal = totalCe + totalPe || 1;
    $("ocSkewCe").style.width = `${(totalCe / oiTotal) * 100}%`;
    $("ocSkewCe").className = "oc-skew-fill oc-skew-fill-ce " + ceOiCls(totalCe);
    $("ocSkewPe").style.width = `${(totalPe / oiTotal) * 100}%`;
    $("ocSkewPe").className = "oc-skew-fill oc-skew-fill-pe " + peOiCls(totalPe);
    $("ocTotalCe").textContent = fmt(totalCe);
    $("ocTotalCe").className = "oc-sum-num " + ceOiCls(totalCe);
    $("ocTotalPe").textContent = fmt(totalPe);
    $("ocTotalPe").className = "oc-sum-num " + peOiCls(totalPe);
    const pcr = totalPe / (totalCe || 1);
    $("ocTotalPcr").textContent = `PCR ${pcr.toFixed(2)}`;

    // ── Chg OI summary (+ how much that shifted PCR today) ──
    const totalCeChg = rows.reduce((s, r) => s + (r.ce.oiChg || 0), 0);
    const totalPeChg = rows.reduce((s, r) => s + (r.pe.oiChg || 0), 0);
    const chgTotal = Math.abs(totalCeChg) + Math.abs(totalPeChg) || 1;
    $("ocChgSkewCe").style.width = `${(Math.abs(totalCeChg) / chgTotal) * 100}%`;
    $("ocChgSkewCe").className = "oc-skew-fill oc-skew-fill-ce " + ceOiCls(totalCeChg);
    $("ocChgSkewPe").style.width = `${(Math.abs(totalPeChg) / chgTotal) * 100}%`;
    $("ocChgSkewPe").className = "oc-skew-fill oc-skew-fill-pe " + peOiCls(totalPeChg);
    $("ocChgTotalCe").textContent = `${sign(totalCeChg)}${fmt(totalCeChg)}`;
    $("ocChgTotalCe").className = "oc-sum-num " + ceOiCls(totalCeChg);
    $("ocChgTotalPe").textContent = `${sign(totalPeChg)}${fmt(totalPeChg)}`;
    $("ocChgTotalPe").className = "oc-sum-num " + peOiCls(totalPeChg);
    const prevCe = totalCe - totalCeChg, prevPe = totalPe - totalPeChg;
    const prevPcr = prevPe / (prevCe || 1);
    const pcrShift = pcr - prevPcr;
    $("ocChgPcrShift").textContent = `PCR Δ ${sign(pcrShift)}${pcrShift.toFixed(2)}`;

    // ── dOI across 5 / 15 / 30m — net PE and CE change per window ──
    // Each column now prints both leg values (PE above, CE below the
    // bars — "double written" so the two numbers behind the bars are
    // actually readable, not just implied by bar height) plus a NET
    // pill (PE − CE) per window so the directional read doesn't require
    // mentally subtracting two numbers yourself.
    const winSums = VEL_WINDOWS.map((w, i) => {
      const ceSum = rows.reduce((s, r) => s + ((r.ce.velTrend && r.ce.velTrend[i]) || 0), 0);
      const peSum = rows.reduce((s, r) => s + ((r.pe.velTrend && r.pe.velTrend[i]) || 0), 0);
      return { w, ceSum, peSum, net: peSum - ceSum };
    });
    const doiHtml = winSums.map(({ w, ceSum, peSum, net }) => {
      const maxAbs = Math.max(1, Math.abs(ceSum), Math.abs(peSum));
      const ceH = Math.max(2, Math.round((Math.abs(ceSum) / maxAbs) * 22));
      const peH = Math.max(2, Math.round((Math.abs(peSum) / maxAbs) * 22));
      const netCls = net > 0 ? "up" : net < 0 ? "down" : "flat";
      return `
        <div class="oc-doi-col">
          <div class="oc-doi-val ${peOiCls(peSum)}">${sign(peSum)}${fmt(peSum)}</div>
          <div class="oc-doi-bars">
            <div class="oc-doi-bar ${peOiCls(peSum)}" style="height:${peH}px;opacity:${peSum < 0 ? .45 : 1};"></div>
            <div class="oc-doi-bar ${ceOiCls(ceSum)}" style="height:${ceH}px;opacity:${ceSum < 0 ? .45 : 1};"></div>
          </div>
          <div class="oc-doi-val ${ceOiCls(ceSum)}">${sign(ceSum)}${fmt(ceSum)}</div>
          <div class="oc-doi-lbl">${w}m</div>
          <div class="oc-doi-net ${netCls}">net ${sign(net)}${fmt(net)}</div>
        </div>`;
    }).join("");
    $("ocDoiGrid").innerHTML = doiHtml;

    // ── Volume / OI ratio — how much of today's activity vs resting OI ──
    const totalCeVol = rows.reduce((s, r) => s + (r.ce.vol || 0), 0);
    const totalPeVol = rows.reduce((s, r) => s + (r.pe.vol || 0), 0);
    const ceRatio = totalCeVol / (totalCe || 1);
    const peRatio = totalPeVol / (totalPe || 1);
    const ratioCap = 3; // visual cap so one outlier strike doesn't flatten the bars
    $("ocVRatio").innerHTML = `
      <div class="oc-vratio-row">
        <span class="oc-vratio-side oc-call-c">CE</span>
        <span class="oc-vratio-num oc-call-c">${fmt(totalCeVol)}</span>
        <div class="oc-vratio-track"><div class="oc-vratio-fill ce" style="width:${Math.min(100, (ceRatio / ratioCap) * 100)}%;"></div></div>
        <span class="oc-vratio-val">${ceRatio.toFixed(2)}x</span>
      </div>
      <div class="oc-vratio-row">
        <span class="oc-vratio-side oc-put-c">PE</span>
        <span class="oc-vratio-num oc-put-c">${fmt(totalPeVol)}</span>
        <div class="oc-vratio-track"><div class="oc-vratio-fill pe" style="width:${Math.min(100, (peRatio / ratioCap) * 100)}%;"></div></div>
        <span class="oc-vratio-val">${peRatio.toFixed(2)}x</span>
      </div>`;

    // ── Net readouts — single signed PE−CE figure on each OI card,
    // instead of a separate analytics block repeating the same totals. ──
    const netOi = totalPe - totalCe;
    $("ocNetOi").innerHTML = `Net (PE−CE) <b>${sign(netOi)}${fmt(netOi)}</b>`;
    $("ocNetOi").className = "oc-sum-net " + signClass(netOi);

    const netChgOi = totalPeChg - totalCeChg;
    $("ocNetChgOi").innerHTML = `Net (PE−CE) <b>${sign(netChgOi)}${fmt(netChgOi)}</b>`;
    $("ocNetChgOi").className = "oc-sum-net " + signClass(netChgOi);
  }

  // strike -> { html, el, greekHtml, greekEl } — lets renderRows() diff
  // per-row instead of tearing down and reparsing the whole <tbody> every
  // tick. A detached <tbody> is used as a throwaway parser context since
  // <tr> markup only parses correctly inside a table.
  const _rowCache = new Map();
  const _parseTr = (html) => {
    const tmp = document.createElement("tbody");
    tmp.innerHTML = html;
    return tmp.firstElementChild;
  };

  function renderRows() {
    const tbody = $("ocBody");
    const rows = visibleRows();
    const wantKeys = new Set(rows.map((r) => String(r.strike)));

    // Drop rows that scrolled out of the visible range (or window shrank).
    for (const [key, entry] of _rowCache) {
      if (wantKeys.has(key)) continue;
      entry.el.remove();
      if (entry.greekEl) entry.greekEl.remove();
      _rowCache.delete(key);
    }

    let afterEl = null; // cursor: last correctly-positioned node so far
    rows.forEach((r) => {
      const key = String(r.strike);
      const mainHtml = buildRowHtml(r);
      const greekHtml = state.greeksOpen ? buildGreekRowHtml(r) : null;
      let entry = _rowCache.get(key);

      if (!entry) {
        entry = { html: mainHtml, el: _parseTr(mainHtml), greekHtml: null, greekEl: null };
        _rowCache.set(key, entry);
      } else if (entry.html !== mainHtml) {
        const fresh = _parseTr(mainHtml);
        entry.el.replaceWith(fresh);
        entry.el = fresh;
        entry.html = mainHtml;
      }

      if (entry.greekHtml !== greekHtml) {
        if (entry.greekEl) { entry.greekEl.remove(); entry.greekEl = null; }
        if (greekHtml) entry.greekEl = _parseTr(greekHtml);
        entry.greekHtml = greekHtml;
      }

      // Reinsert only if not already immediately after the cursor —
      // avoids a DOM move (and the reflow that goes with it) on every
      // unchanged row when nothing about ordering has shifted.
      const wantAfter = afterEl ? afterEl.nextSibling : tbody.firstChild;
      if (entry.el !== wantAfter) tbody.insertBefore(entry.el, wantAfter);
      if (entry.greekEl && entry.el.nextSibling !== entry.greekEl) {
        tbody.insertBefore(entry.greekEl, entry.el.nextSibling);
      }
      afterEl = entry.greekEl || entry.el;
    });
  }

  function renderAll() {
    renderHeader();
    renderSummary();
    renderRows();
    $("ocVelLabel").textContent = "5 · 15 · 30m";
  }

  // ── STRIKE DRAWER ──
  // mode "summary" (row click) shows the LTP/IV/OI/volume read for both
  // legs; mode "depth" (strike-cell click) shows Bid/Ask quotes and
  // total buy/sell depth instead. Same panel element, different content.
  function openDrawer(strike, mode) {
    const r = state.rows.find((x) => x.strike === strike);
    if (!r) return;
    state.selectedStrike = strike;
    document.querySelectorAll(".oc-row").forEach((tr) => tr.classList.toggle("oc-selected", +tr.dataset.strike === strike));
    $("ocDrawerPanel").innerHTML = mode === "depth" ? buildDepthDrawerHtml(r) : buildSummaryDrawerHtml(r);
    $("ocDrawer").classList.add("open");
  }

  function buildDepthDrawerHtml(r) {
    const hasDepth = r.ce.bid != null || r.ce.ask != null || r.pe.bid != null || r.pe.ask != null;
    const depthBar = (totBid, totAsk) => {
      const total = (totBid || 0) + (totAsk || 0) || 1;
      const buyShare = Math.round(((totBid || 0) / total) * 100);
      return `<div class="oc-depth-bar-track"><div class="oc-depth-bar-buy" style="width:${buyShare}%;"></div><div class="oc-depth-bar-sell" style="width:${100 - buyShare}%;"></div></div>`;
    };
    const legDepth = (label, leg, colorVar) => `
      <div class="oc-depth-side">
        <div class="oc-depth-label" style="color:var(${colorVar});">${label}</div>
        <div class="oc-depth-quote">Bid <b>${leg.bid != null ? fmtNum(leg.bid) : "—"}</b> ×${fmt(leg.bidQty)} &nbsp;/&nbsp; Ask <b>${leg.ask != null ? fmtNum(leg.ask) : "—"}</b> ×${fmt(leg.askQty)}</div>
        ${depthBar(leg.totalBidQty, leg.totalAskQty)}
        <div class="oc-depth-totals"><span>Total Buy ${fmt(leg.totalBidQty)}</span><span>Total Sell ${fmt(leg.totalAskQty)}</span></div>
      </div>`;
    return `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
        <div style="font-family:var(--display);font-weight:700;font-size:17px;">${state.symbol} ${r.strike} <span style="color:var(--text-3);font-size:11px;font-weight:500;">Bid/Ask Depth</span>${r.isAtm ? ' <span style="color:var(--spine);font-size:11px;">ATM</span>' : ""}</div>
        <button onclick="document.getElementById('ocDrawer').classList.remove('open')" style="background:none;border:none;color:var(--text-2);font-size:18px;cursor:pointer;">✕</button>
      </div>
      ${hasDepth
        ? legDepth("CALL", r.ce, "--call") + legDepth("PUT", r.pe, "--put")
        : `<div style="font-size:12px;color:var(--text-3);">No depth data in this feed yet.</div>`}`;
  }

  function buildSummaryDrawerHtml(r) {
    const strike = r.strike;
    return `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
        <div style="font-family:var(--display);font-weight:700;font-size:17px;">${state.symbol} ${strike}${r.isAtm ? ' <span style="color:var(--spine);font-size:11px;">ATM</span>' : ""}</div>
        <button onclick="document.getElementById('ocDrawer').classList.remove('open')" style="background:none;border:none;color:var(--text-2);font-size:18px;cursor:pointer;">✕</button>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div>
          <div style="color:var(--call);font-weight:700;font-size:12px;margin-bottom:8px;">CALL · ${SIGNAL_LABEL[r.ce.signal] || "—"}</div>
          <div style="font-family:var(--mono);font-size:12.5px;line-height:2;color:var(--text-2);">
            LTP <b style="color:var(--text);">${fmtNum(r.ce.ltp)}</b><br>
            IV <b style="color:var(--text);">${fmtNum(r.ce.iv)}%</b><br>
            OI <b style="color:var(--text);">${fmt(r.ce.oi)}</b> &nbsp; Chg <b style="color:var(--text);">${sign(r.ce.oiChg)}${fmt(r.ce.oiChg)}</b><br>
            Volume <b style="color:var(--text);">${fmt(r.ce.vol)}</b>
          </div>
        </div>
        <div>
          <div style="color:var(--put);font-weight:700;font-size:12px;margin-bottom:8px;">PUT · ${SIGNAL_LABEL[r.pe.signal] || "—"}</div>
          <div style="font-family:var(--mono);font-size:12.5px;line-height:2;color:var(--text-2);">
            LTP <b style="color:var(--text);">${fmtNum(r.pe.ltp)}</b><br>
            IV <b style="color:var(--text);">${fmtNum(r.pe.iv)}%</b><br>
            OI <b style="color:var(--text);">${fmt(r.pe.oi)}</b> &nbsp; Chg <b style="color:var(--text);">${sign(r.pe.oiChg)}${fmt(r.pe.oiChg)}</b><br>
            Volume <b style="color:var(--text);">${fmt(r.pe.vol)}</b>
          </div>
        </div>
      </div>
      <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--hairline);font-family:var(--mono);font-size:12px;color:var(--text-2);">
        PCR <b style="color:var(--spine);">${r.pcr}</b> (${r.pcrChg}) — put OI share ${((r.pe.oi/(r.pe.oi+r.ce.oi||1))*100).toFixed(0)}% of this strike
      </div>`;
  }

  // ── EVENTS ──
  function wireEvents() {
    $("ocBack").addEventListener("click", () => {
      if (window.opener) { window.close(); }
      // DashboardPro.html lives one level up now that this page moved
      // into OptionChain/ — was a same-folder "DashboardPro.html" before.
      else { history.length > 1 ? history.back() : (location.href = "../DashboardPro.html"); }
    });

    $("ocExpiry").addEventListener("change", (e) => {
      state.expiry = e.target.value;
      if (_ocRequestExpiry) _ocRequestExpiry(state.expiry); // hook for live integration
      renderAll();
    });

    $("ocRangeGroup").addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      state.range = +btn.dataset.val;
      $("ocRangeGroup").dataset.active = state.range;
      $("ocRangeGroup").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      renderSummary();
      renderRows();
    });

    $("ocVelGroup").addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      state.velWin = +btn.dataset.val;
      $("ocVelGroup").dataset.active = state.velWin;
      $("ocVelGroup").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    });

    // Greeks toggle is the ONLY thing that flips state.greeksOpen, and
    // renderRows() is the only place that reads it — so the greek rows
    // stay visible across row clicks, strike clicks, and range/vel
    // changes, and close only when this button is pressed again.
    $("ocGreeksBtn").addEventListener("click", () => {
      state.greeksOpen = !state.greeksOpen;
      $("ocGreeksBtn").classList.toggle("active", state.greeksOpen);
      renderRows();
    });

    $("ocBody").addEventListener("click", (e) => {
      const tr = e.target.closest(".oc-row");
      if (tr) openDrawer(+tr.dataset.strike, "summary");
    });

    $("ocDrawer").addEventListener("click", (e) => {
      if (e.target.id === "ocDrawer") $("ocDrawer").classList.remove("open");
    });

    $("ocTradeModal").addEventListener("click", (e) => {
      if (e.target.id === "ocTradeModal") closeTradeModal();
    });

    // set initial toggle button active states
    $("ocRangeGroup").querySelector(`button[data-val="${state.range}"]`)?.classList.add("active");
    $("ocVelGroup").querySelector(`button[data-val="${state.velWin}"]`)?.classList.add("active");
  }

  // ── BUY/SELL QUICK-ORDER MODAL (LTP click) ──
  function openTradeModal(strike, side, ltp) {
    const r = state.rows.find((x) => x.strike === strike);
    if (!r) return;
    const leg = side === "CE" ? r.ce : r.pe;
    const colorVar = side === "CE" ? "--call" : "--put";
    $("ocTradePanel").innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px;">
        <div style="font-family:var(--display);font-weight:700;font-size:16px;">${state.symbol} ${strike} <span style="color:var(${colorVar});">${side}</span></div>
        <button onclick="window.ocCloseTradeModal()" style="background:none;border:none;color:var(--text-2);font-size:18px;cursor:pointer;">✕</button>
      </div>
      <div style="font-family:var(--mono);font-size:12px;color:var(--text-3);margin-bottom:14px;">LTP <b style="color:var(--text);">${ltp != null ? fmtNum(leg.ltp) : "—"}</b></div>
      <label style="font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--text-3);">Lots</label>
      <input type="number" class="oc-trade-qty" id="ocTradeQty" value="1" min="1" step="1">
      <div class="oc-trade-btns">
        <button class="oc-trade-btn oc-trade-btn-buy" onclick="window.ocPlaceOrder('BUY')">Buy</button>
        <button class="oc-trade-btn oc-trade-btn-sell" onclick="window.ocPlaceOrder('SELL')">Sell</button>
      </div>
      <div class="oc-trade-confirm" id="ocTradeConfirm"></div>`;
    state._tradeCtx = { strike, side, ltp: leg.ltp };
    $("ocTradeModal").classList.add("open");
  }

  function closeTradeModal() {
    $("ocTradeModal").classList.remove("open");
  }

  function placeOrder(action) {
    const ctx = state._tradeCtx;
    if (!ctx) return;
    const qty = Math.max(1, +($("ocTradeQty")?.value || 1));
    // Hook for live integration — the dashboard's paper-trading engine
    // (or a real order-routing layer) can register this to actually
    // execute the order; falls back to an inline confirmation so the
    // modal is fully usable standalone.
    if (window._ocPlaceOrder) {
      window._ocPlaceOrder({ symbol: state.symbol, expiry: state.expiry, ...ctx, qty, action });
    }
    const el = $("ocTradeConfirm");
    if (el) {
      el.textContent = `${action === "BUY" ? "Bought" : "Sold"} ${qty} lot${qty > 1 ? "s" : ""} of ${state.symbol} ${ctx.strike} ${ctx.side} @ ${fmtNum(ctx.ltp)}`;
      el.classList.add("show");
    }
    // Previously the modal never closed after Buy/Sell — placeOrder() only
    // ever set the confirm text, with nothing calling closeTradeModal().
    // Clear _tradeCtx immediately so a stray double-click can't resubmit
    // the same order while the confirm message is still showing, then
    // close shortly after so the confirmation is actually readable first.
    state._tradeCtx = null;
    setTimeout(closeTradeModal, 900);
  }

  // exposed for inline onclick handlers in row/strike/LTP cells and modal buttons
  window.ocOpenTradeModal = openTradeModal;
  window.ocCloseTradeModal = closeTradeModal;
  window.ocPlaceOrder = placeOrder;
  window.ocOpenDepth = (strike) => openDrawer(strike, "depth");

  // ── LIVE DATA INTEGRATION ──
  function applyLivePayload(msg) {
    if (!msg || !msg.rows) return;
    state.rows = msg.rows;
    if (msg.symbol) state.symbol = msg.symbol;
    if (msg.spot != null) state.spot = msg.spot;
    if (msg.spotChg != null) state.spotChg = msg.spotChg;
    if (msg.spotChgPct != null) state.spotChgPct = msg.spotChgPct;
    if (msg.expiry) state.expiry = msg.expiry;
    if (msg.expiryDates) state.expiryDates = msg.expiryDates;
    // Keep this tab's range in sync with the main dashboard's sidebar
    // toggle — chain-sync.js has always sent this field, but nothing
    // here ever read it, so the two views could silently show different
    // ATM ranges with no indication either was out of sync.
    if (msg.range != null && msg.range !== state.range) {
      state.range = msg.range;
      const activeBtn = $("ocRangeGroup").querySelector(`button[data-val="${msg.range}"]`);
      if (activeBtn) {
        $("ocRangeGroup").dataset.active = msg.range;
        $("ocRangeGroup").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === activeBtn));
      }
    }
    renderAll();
  }

  function initLiveSync() {
    // Preferred path: BroadcastChannel from the main dashboard tab.
    if ("BroadcastChannel" in window) {
      const chan = new BroadcastChannel("oc-live-sync");
      chan.addEventListener("message", (e) => applyLivePayload(e.data));
      // ask the dashboard tab (if any) to replay its last snapshot immediately
      chan.postMessage({ type: "oc-request-snapshot" });
      // Wires up the expiry-dropdown hook (see wireEvents' ocExpiry change
      // listener) — this was previously just a comment ("hook for live
      // integration") with nothing ever assigning window._ocRequestExpiry,
      // so picking a new expiry here updated state.expiry locally and
      // re-rendered the SAME rows, making the dropdown look inert. Posting
      // over the same channel the dashboard already listens on lets
      // chain-views.js drive the real #expirySelect and let its existing
      // change handler do the actual chain switch. Kept as a closure
      // variable (module state), not window._ocRequestExpiry — nothing
      // outside this IIFE ever needs to read it.
      _ocRequestExpiry = (expiry) => {
        chan.postMessage({ type: "oc-request-expiry", expiry });
      };
    }
    // Fallback path: this page was opened via window.open() from the
    // dashboard, which posts messages directly to us.
    window.addEventListener("message", (e) => {
      if (e.data && e.data.rows) applyLivePayload(e.data);
    });
  }

  // ── BOOT ──
  function boot() {
    state.rows = buildDemoRows();
    wireEvents();
    renderAll();
    initLiveSync();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
