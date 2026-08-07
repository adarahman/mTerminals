/* ════════════════════════════════════════════════════════════════════
   OPTION CHAIN — standalone page logic

   Row shape consumed here is deliberately identical to what
   ChainDenseView.mapPayloadToRows() in chain-views.js already produces:

     { strike, isAtm, footprintScore, pcr, pcrChg,
       ce: { iv, ivChg, vol, ltp, chg, oi, oiChg, signal },
       pe: { iv, ivChg, vol, ltp, chg, oi, oiChg, signal },
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

   Demo rows are available only when this page is opened explicitly with
   ?demo=1. In normal production mode, no live feed means CONNECTING /
   DISCONNECTED with unavailable market values — fabricated rows are never
   presented as a live market.
   ════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const DEMO_MODE = new URLSearchParams(window.location.search).get("demo") === "1";
  const FEED_CONNECT_TIMEOUT_MS = 5000;
  const FEED_STALE_MS = 10000;
  const FEED_DISCONNECTED_MS = 30000;

  // ── STATE ──
  let state = {
    symbol: "NIFTY",
    spot: null,
    spotChg: null,
    spotChgPct: null,
    expiry: "",
    expiryDates: [],
    rows: [],
    range: 10,
    greeksOpen: false,
    selectedStrike: null,
    drawerMode: null,
    drawerSignature: null,
    drawerInvoker: null,
    tradeInvoker: null,
    focusStrike: null,
    pendingFocusStrike: null,
    requestedExpiry: null,
    needsInitialAtmCenter: false,
    contextKey: "",
    // Real server-fed vol/OI ratio + max-pain strike, filled in by
    // applyLivePayload() once chain-sync.js starts forwarding them —
    // empty/null until the first live broadcast arrives, same as demo
    // mode's other fields.
    volOiRatios: {},
    maxPain: null,
    feedState: DEMO_MODE ? "DEMO" : "CONNECTING",
    bootAt: Date.now(),
    lastLiveAt: null,
  };
  // Set by initLiveSync() when a BroadcastChannel to the dashboard tab is
  // open; used by the expiry dropdown's change handler to ask the
  // dashboard to drive the real expiry switch. Module state (closure
  // variable), not window._ocRequestExpiry — nothing outside this IIFE
  // ever needs to read it.
  let _ocRequestExpiry = null;
  let _ocRequestRange = null;
  // Same BroadcastChannel instance initLiveSync() opens, kept here (not on
  // window — same reasoning as _ocRequestExpiry above) so placeOrder() can
  // route an order request over it when this page has no parent window to
  // call into directly (i.e. opened standalone, not via the dashboard's
  // embedded iframe). null until initLiveSync() runs, and stays null in
  // browsers without BroadcastChannel support.
  let _ocOrderChan = null;

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
        oi: r.ceOI, oiChg: r.ceOIchg, signal: r.ceSig,
        bid: r.ceBid, bidQty: r.ceBidQty, ask: r.ceAsk, askQty: r.ceAskQty,
        totalBidQty: r.ceTotBidQty, totalAskQty: r.ceTotAskQty,
        delta: r.ceDelta, gamma: r.ceGamma, theta: r.ceTheta, vega: r.ceVega,
        // Demo-only approximation (OI x LTP) so the Premium ₹ column isn't
        // blank before a live payload arrives — real data overwrites this
        // via applyLivePayload()/mapPayloadToRows(), which read the actual
        // cePremiumLocked/pePremiumLocked backend fields instead.
        premiumLocked: (r.ceOI || 0) * (r.ceLTP || 0),
      },
      pe: {
        iv: r.peIV, ivChg: r.peIVchg, vol: r.peVol, ltp: r.peLTP, chg: r.peChg, chgPct: r.peChgPct,
        oi: r.peOI, oiChg: r.peOIchg, signal: r.peSig,
        bid: r.peBid, bidQty: r.peBidQty, ask: r.peAsk, askQty: r.peAskQty,
        totalBidQty: r.peTotBidQty, totalAskQty: r.peTotAskQty,
        delta: r.peDelta, gamma: r.peGamma, theta: r.peTheta, vega: r.peVega,
        premiumLocked: (r.peOI || 0) * (r.peLTP || 0),
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

  // ── FEED STATE ──
  function renderFeedState() {
    const status = state.feedState || "DISCONNECTED";
    const eyebrow = $("ocEyebrow");
    const pill = $("ocFeedStatus");
    if (eyebrow) eyebrow.dataset.feedState = status;
    if (pill) {
      pill.textContent = status;
      pill.className = "oc-feed-status is-" + status.toLowerCase().replace(/\s+/g, "-");
    }
  }

  function setFeedState(next) {
    if (!next || state.feedState === next) return;
    state.feedState = next;
    renderFeedState();
    if (!state.rows.length) renderRows();
  }

  function startFeedMonitor() {
    renderFeedState();
    if (DEMO_MODE) return;
    const check = () => {
      const now = Date.now();
      if (state.lastLiveAt == null) {
        setFeedState(now - state.bootAt >= FEED_CONNECT_TIMEOUT_MS ? "DISCONNECTED" : "CONNECTING");
        return;
      }
      const age = now - state.lastLiveAt;
      if (age >= FEED_DISCONNECTED_MS) setFeedState("DISCONNECTED");
      else if (age >= FEED_STALE_MS) setFeedState("STALE");
      else setFeedState("LIVE");
    };
    check();
    state._feedTimer = setInterval(check, 1000);
  }

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
  const fmtNum = (v, d = 2) => (v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(d));
  const fmtPct = (v, d = 2) => (v == null || !Number.isFinite(Number(v)) ? "—" : `${Number(v).toFixed(d)}%`);
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
    // Display signals
    "strong-bullish": 3,
    "bullish": 1,
    "mixed": 0,
    "neutral": 0,
    "bearish": -1,
    "strong-bearish": -3,

    // Option OI + Price action signals
    "Buying BuildUp": 2,      // Bullish
    "Short Covering": 2,      // Bullish

    "Writing BuildUp": -2,    // Bearish
    "Long Unwinding": -2      // Bearish
};
  function compositeSignal(ceSig, peSig) {
    ceSig = (ceSig || "").trim();
    peSig = (peSig || "").trim();

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

  // ── INSTITUTIONAL FOOTPRINT / MARKET STRUCTURE ──
  // Market Structure still uses the shared marketStructureLabels() helper.
  // Institutional significance is owned by backend footprintScore and is
  // presented directly below — no local institutional threshold engine.
  // D-05 presents footprintScore directly instead of re-running the old
  // median-OI / Vol-OI institutional threshold engine in this UI.
  function canonicalFootprintBadge(r) {
    if (r.footprintScore == null || r.footprintScore === "") {
      return { label: "—", color: "var(--text-3)", title: "Institutional footprint unavailable" };
    }
    const score = Number(r.footprintScore);
    if (!Number.isFinite(score)) {
      return { label: "—", color: "var(--text-3)", title: "Institutional footprint unavailable" };
    }
    const ceFlow = Math.abs(Number(r.ce && r.ce.capitalFlow) || 0);
    const peFlow = Math.abs(Number(r.pe && r.pe.capitalFlow) || 0);
    const dominantSide = ceFlow >= peFlow ? "CE" : "PE";
    const color = score >= 70 ? "var(--put)" : score >= 40 ? "var(--spine)" : "var(--text-3)";
    return {
      label: `FP ${score.toFixed(0)}`,
      color,
      title: `Canonical footprint ${score.toFixed(1)}/100 · ${dominantSide}-led`,
    };
  }

  function computeStrikeAnalytics(rows) {
    if (!rows.length) return { structure: {}, smartMoney: {} };
    const atmRow = rows.find((r) => r.isAtm);
    const atm = atmRow ? atmRow.strike : rows[0].strike;

    const oiByStrike = {};
    rows.forEach((r) => {
      oiByStrike[r.strike] = {
        ce: r.ce.oi || 0, pe: r.pe.oi || 0,
        ceChg: r.ce.oiChg || 0, peChg: r.pe.oiChg || 0,
      };
    });
    const structure = marketStructureLabels(rows, atm, oiByStrike, state.maxPain);
    const smartMoney = {};
    rows.forEach((r) => { smartMoney[r.strike] = canonicalFootprintBadge(r); });
    return { structure, smartMoney };
  }

  function smartMoneyCellHtml(analytics, strike) {
    const sm = analytics.smartMoney[strike];
    if (!sm) return "";

    return `
        <span class="oc-smart-abbr"
              style="color:${sm.color};"
              title="${sm.title || sm.label}">
            <span class="oc-smart-dot"
                  style="background:${sm.color};"></span>
            ${sm.label}
        </span>
    `;
}

  function structureCellHtml(analytics, strike) {
    const st = analytics.structure[strike];
    if (!st) return "";

    return `
        <span class="oc-struct-abbr"
              style="color:${st.color};"
              title="${st.text}">
            ${st.text}
        </span>
    `;
}

  // ── ROW RENDER ──
  function buildRowHtml(r, analytics) {
    analytics = analytics || { structure: {}, smartMoney: {} };
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
    <tr class="oc-row${r.isAtm ? " oc-atm" : ""}${state.focusStrike === r.strike ? " oc-focus-target" : ""}${state.selectedStrike === r.strike ? " oc-selected" : ""}" data-strike="${r.strike}" tabindex="0" aria-label="${state.symbol} ${r.strike} strike row; press Enter for summary">
      <td class="oc-iv-cell">
        <div class="oc-stack">
          <span class="pe">${fmtPct(r.pe.iv)}</span>
          <span class="ce">${fmtPct(r.ce.iv)}</span>
        </div>
      </td>
      <td class="oc-vol-cell">
        <div class="oc-stack">
          <span class="pe">${fmt(r.pe.vol)}</span>
          <span class="ce">${fmt(r.ce.vol)}</span>
        </div>
      </td>
      <td class="oc-vol-cell" title="Premium locked (OI x LTP) — oi.capital_metrics">
        <div class="oc-stack">
          <span class="pe">₹${fmt(r.pe.premiumLocked)}</span>
          <span class="ce">₹${fmt(r.ce.premiumLocked)}</span>
        </div>
      </td>
      <td class="oc-ltp-cell" title="Open CE quick order">
        <button class="oc-cell-action oc-ltp-action" data-oc-action="trade-ce" onclick="event.stopPropagation();window.ocOpenTradeModal(${r.strike},'CE',${r.ce.ltp != null ? r.ce.ltp : "null"})" aria-label="Trade ${state.symbol} ${r.strike} CE">
          <span class="oc-ltp-main oc-call-c">${fmtNum(r.ce.ltp)}</span>
          <span class="oc-ltp-sub ${signClass(r.ce.chg)}">${ltpChgStr(r.ce.chg, r.ce.chgPct)}</span>
        </button>
      </td>
      <td class="oc-ltp-cell oc-ltp-adjacent" title="Open PE quick order">
        <button class="oc-cell-action oc-ltp-action" data-oc-action="trade-pe" onclick="event.stopPropagation();window.ocOpenTradeModal(${r.strike},'PE',${r.pe.ltp != null ? r.pe.ltp : "null"})" aria-label="Trade ${state.symbol} ${r.strike} PE">
          <span class="oc-ltp-main oc-put-c">${fmtNum(r.pe.ltp)}</span>
          <span class="oc-ltp-sub ${signClass(r.pe.chg)}">${ltpChgStr(r.pe.chg, r.pe.chgPct)}</span>
        </button>
      </td>
      <td class="oc-strike-cell" title="Open Bid/Ask depth">
        <button class="oc-cell-action oc-strike-action" data-oc-action="depth" onclick="event.stopPropagation();window.ocOpenDepth(${r.strike})" aria-label="Open Bid Ask depth for strike ${r.strike}">
          <span class="oc-strike-val">${r.strike}${ceVsPeDivergence(r.ce.chg, r.pe.chg) ? `<i class="oc-strike-div ${ceVsPeDivergence(r.ce.chg, r.pe.chg)}" title="${ceVsPeDivergence(r.ce.chg, r.pe.chg) === 'div-red' ? 'CE up / PE down' : 'CE down / PE up'}"></i>` : ""}</span>
          <div class="oc-strike-gauge">
            <div class="oc-strike-gauge-pe" style="width:${gaugePe}%;"></div>
            <div class="oc-strike-gauge-ce" style="width:${gaugeCe}%;"></div>
          </div>
          <span class="oc-strike-pcr">PCR <b>${r.pcr}</b> <span class="${signClass(parseFloat(r.pcrChg))}">${r.pcrChg}</span></span>
        </button>
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
      <td class="oc-sig-cell">${badge(compositeSignal(r.ce.signal, r.pe.signal))}</td>
      <td class="oc-smart-cell">${smartMoneyCellHtml(analytics, r.strike)}</td>
      <td class="oc-struct-cell">${structureCellHtml(analytics, r.strike)}</td>
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
      <td colspan="11">
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
    renderFeedState();
    $("ocSpot").textContent = state.spot == null || !Number.isFinite(Number(state.spot))
      ? "—"
      : Number(state.spot).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const chgEl = $("ocSpotChg");
    const haveChg = state.spotChg != null && state.spotChgPct != null
      && Number.isFinite(Number(state.spotChg)) && Number.isFinite(Number(state.spotChgPct));
    const up = haveChg && Number(state.spotChg) >= 0;
    chgEl.textContent = haveChg
      ? `${up ? "+" : ""}${Number(state.spotChg).toFixed(2)} (${Number(state.spotChgPct) >= 0 ? "+" : ""}${Number(state.spotChgPct).toFixed(2)}%)`
      : "—";
    chgEl.className = "oc-spot-chg" + (haveChg && !up ? " down" : "");
    document.querySelector("#ocSymbol").childNodes[0].nodeValue = (state.symbol || "—") + " ";

    const sel = $("ocExpiry");
    const sortedExpiryDates = sortExpiryDates(state.expiryDates || []);
    const expiryKey = sortedExpiryDates.join("|");
    if (!sortedExpiryDates.length) {
      if (sel.dataset.key !== "__empty__") {
        sel.innerHTML = `<option value="">Waiting for feed…</option>`;
        sel.dataset.key = "__empty__";
      }
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    if (sel.dataset.key !== expiryKey) {
      sel.innerHTML = sortedExpiryDates.map((d) => `<option value="${d}"${d === state.expiry ? " selected" : ""}>${d}</option>`).join("");
      sel.dataset.key = expiryKey;
    } else if (sel.value !== state.expiry) {
      // The expiryDates LIST is unchanged (same key, so the innerHTML
      // rebuild above was skipped) but the ACTIVE expiry moved — e.g. the
      // main dashboard tab switched expiry for the same symbol via its
      // own #expirySelect, which broadcasts a new msg.expiry over
      // oc-live-sync while msg.expiryDates stays byte-identical. Without
      // this branch the <select> here kept showing whatever expiry was
      // selected on the LAST innerHTML rebuild — stale relative to
      // state.rows, which applyLivePayload() already updated to the new
      // expiry's data — so the dropdown label and the ledger underneath
      // it visibly disagreed. Mirrors the equivalent fallback in the main
      // dashboard's own ChainDenseView.renderExpiryOptions (chain-renderer.js).
      sel.value = state.expiry;
    }
    renderExpiryPendingState();
  }

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

    if (!rows.length) {
      $("ocSkewCe").style.width = "0%";
      $("ocSkewPe").style.width = "0%";
      $("ocChgSkewCe").style.width = "0%";
      $("ocChgSkewPe").style.width = "0%";
      $("ocTotalCe").textContent = "—";
      $("ocTotalPe").textContent = "—";
      $("ocTotalPcr").textContent = "PCR —";
      $("ocChgTotalCe").textContent = "—";
      $("ocChgTotalPe").textContent = "—";
      $("ocChgPcrShift").textContent = "PCR Δ —";
      $("ocVRatio").innerHTML = `<div style="color:var(--oc-text-3);font-family:var(--font-mono);font-size:10px;">—</div>`;
      $("ocNetOi").innerHTML = `Net (PE−CE) <b>—</b>`;
      $("ocNetChgOi").innerHTML = `Net (PE−CE) <b>—</b>`;
      return;
    }

    // ── OI summary ──
    const totalCe = rows.reduce((s, r) => s + r.ce.oi, 0);
    const totalPe = rows.reduce((s, r) => s + r.pe.oi, 0);
    const oiTotal = totalCe + totalPe || 1;
    $("ocSkewCe").style.width = `${(totalCe / oiTotal) * 100}%`;
    $("ocSkewCe").className = "oc-skew-fill oc-skew-fill-ce oc-fixed-ce";
    $("ocSkewPe").style.width = `${(totalPe / oiTotal) * 100}%`;
    $("ocSkewPe").className = "oc-skew-fill oc-skew-fill-pe oc-fixed-pe";
    $("ocTotalCe").textContent = fmt(totalCe);
    $("ocTotalCe").className = "oc-sum-num oc-call-c";
    $("ocTotalPe").textContent = fmt(totalPe);
    $("ocTotalPe").className = "oc-sum-num oc-put-c";
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
    // Judged against the FULL chain (state.rows), not just the visible
    // ±range window, so a strike's Smart Money / Market Structure read
    // can't shift just because the range toggle changed what's on screen.
    const analytics = computeStrikeAnalytics(state.rows);
    const wantKeys = new Set(rows.map((r) => String(r.strike)));

    if (!rows.length) {
      for (const [, entry] of _rowCache) {
        entry.el.remove();
        if (entry.greekEl) entry.greekEl.remove();
      }
      _rowCache.clear();
      const message = state.feedState === "DEMO"
        ? "Demo mode — no live market connection"
        : state.feedState === "CONNECTING"
          ? "Connecting to live Option Chain…"
          : state.feedState === "LIVE"
            ? "Live feed connected — no Option Chain rows for this context"
            : state.feedState === "STALE"
              ? "Live Option Chain is stale — waiting for a fresh snapshot"
              : "No live Option Chain feed — open/refresh the Dashboard connection";
      tbody.innerHTML = `<tr class="oc-empty-row" id="ocEmptyRow"><td colspan="11">${message}</td></tr>`;
      return;
    }
    const emptyRow = $("ocEmptyRow");
    if (emptyRow) emptyRow.remove();

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
      const mainHtml = buildRowHtml(r, analytics);
      const greekHtml = state.greeksOpen ? buildGreekRowHtml(r) : null;
      let entry = _rowCache.get(key);

      if (!entry) {
        entry = { html: mainHtml, el: _parseTr(mainHtml), greekHtml: null, greekEl: null };
        _rowCache.set(key, entry);
      } else if (entry.html !== mainHtml) {
        const active = document.activeElement;
        const rowHadFocus = active === entry.el;
        const actionKey = active && entry.el.contains(active) ? active.dataset.ocAction : null;
        const fresh = _parseTr(mainHtml);
        entry.el.replaceWith(fresh);
        entry.el = fresh;
        entry.html = mainHtml;
        if (rowHadFocus || actionKey) {
          requestAnimationFrame(() => {
            const target = rowHadFocus ? fresh : fresh.querySelector(`[data-oc-action="${actionKey}"]`);
            if (target) target.focus({preventScroll:true});
          });
        }
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

    if ($("ocDrawer").classList.contains("open")) refreshOpenDrawer();
  }

  function _syncRangeButtons(){
    const grp = $("ocRangeGroup");
    if(!grp) return;
    grp.dataset.active = state.range;
    grp.querySelectorAll("button").forEach((b) => b.classList.toggle("active", +b.dataset.val === state.range));
  }

  function focusStrike(strike){
    const n = Number(strike);
    if(!Number.isFinite(n)) return;
    const idx = state.rows.findIndex((r) => +r.strike === n);
    if(idx < 0){ state.pendingFocusStrike = n; return; }

    state.pendingFocusStrike = null;
    const atmIdx = state.rows.findIndex((r) => r.isAtm);
    if(state.range < 9999 && atmIdx >= 0){
      const required = Math.abs(idx-atmIdx);
      if(required > state.range){
        const allowed = [3,5,10,15,9999];
        state.range = allowed.find((v) => v >= required) || 9999;
        _syncRangeButtons();
        if (_ocRequestRange) _ocRequestRange(state.range);
        renderSummary();
      }
    }

    state.focusStrike = n;
    renderRows();
    requestAnimationFrame(() => {
      const row = document.querySelector(`.oc-row[data-strike="${n}"]`);
      if(row) row.scrollIntoView({behavior:'smooth', block:'center'});
    });
    clearTimeout(state._focusTimer);
    state._focusTimer = setTimeout(() => {
      state.focusStrike = null;
      const row = document.querySelector(`.oc-row[data-strike="${n}"]`);
      if(row) row.classList.remove('oc-focus-target');
    }, 2200);
  }

  function centerAtmOnce() {
    if (!state.needsInitialAtmCenter || state.pendingFocusStrike != null) return;
    const atm = state.rows.find((r) => r.isAtm);
    if (!atm) return;
    state.needsInitialAtmCenter = false;
    requestAnimationFrame(() => {
      const row = document.querySelector(`.oc-row[data-strike="${atm.strike}"]`);
      if (row) row.scrollIntoView({behavior:'auto', block:'center'});
    });
  }

  function renderAll() {
    renderHeader();
    renderSummary();
    renderRows();
  }

  // ── STRIKE DRAWER ──
  // mode "summary" (row click) shows the LTP/IV/OI/volume read for both
  // legs; mode "depth" (strike-cell click) shows Bid/Ask quotes and
  // total buy/sell depth instead. Same panel element, different content.
  function captureRowInvoker(strike) {
    const active = document.activeElement;
    const row = document.querySelector(`.oc-row[data-strike="${strike}"]`);
    const activeRow = active && active.closest ? active.closest(".oc-row") : null;
    return {
      el: active && active !== document.body ? active : row,
      strike: activeRow ? Number(activeRow.dataset.strike) : Number(strike),
      action: active && active.dataset ? active.dataset.ocAction || null : null,
      rowFocus: !!row && active === row,
    };
  }

  function restoreRowInvoker(ref) {
    if (!ref) return;
    if (ref.el && document.contains(ref.el)) { ref.el.focus({preventScroll:true}); return; }
    const row = document.querySelector(`.oc-row[data-strike="${ref.strike}"]`);
    if (!row) return;
    const target = ref.action ? row.querySelector(`[data-oc-action="${ref.action}"]`) : row;
    if (target) target.focus({preventScroll:true});
  }

  function drawerSignature(r, mode) {
    if (!r) return "";
    if (mode === "depth") {
      return JSON.stringify([r.strike,r.ce.bid,r.ce.bidQty,r.ce.ask,r.ce.askQty,r.ce.totalBidQty,r.ce.totalAskQty,r.pe.bid,r.pe.bidQty,r.pe.ask,r.pe.askQty,r.pe.totalBidQty,r.pe.totalAskQty]);
    }
    return JSON.stringify([r.strike,r.pcr,r.pcrChg,r.ce.ltp,r.ce.iv,r.ce.oi,r.ce.oiChg,r.ce.vol,r.ce.premiumLocked,r.ce.capitalFlow,r.ce.signal,r.pe.ltp,r.pe.iv,r.pe.oi,r.pe.oiChg,r.pe.vol,r.pe.premiumLocked,r.pe.capitalFlow,r.pe.signal]);
  }

  function openDrawer(strike, mode) {
    const r = state.rows.find((x) => x.strike === strike);
    if (!r) return;
    state.drawerInvoker = captureRowInvoker(strike);
    state.selectedStrike = strike;
    state.drawerMode = mode === "depth" ? "depth" : "summary";
    state.drawerSignature = drawerSignature(r, state.drawerMode);
    document.querySelectorAll(".oc-row").forEach((tr) => tr.classList.toggle("oc-selected", +tr.dataset.strike === strike));
    $("ocDrawerPanel").innerHTML = state.drawerMode === "depth" ? buildDepthDrawerHtml(r) : buildSummaryDrawerHtml(r);
    $("ocDrawer").classList.add("open");
    requestAnimationFrame(() => $("ocDrawerPanel").querySelector('[data-oc-drawer-action="close"]')?.focus());
  }

  function refreshOpenDrawer() {
    const strike = state.selectedStrike;
    const r = state.rows.find((x) => x.strike === strike);
    if (!r || !$("ocDrawer").classList.contains("open")) return;
    const sig = drawerSignature(r, state.drawerMode);
    if (sig === state.drawerSignature) return;
    state.drawerSignature = sig;
    const panel = $("ocDrawerPanel");
    const active = document.activeElement;
    const actionKey = active && panel.contains(active) ? active.dataset.ocDrawerAction : null;
    panel.innerHTML = state.drawerMode === "depth" ? buildDepthDrawerHtml(r) : buildSummaryDrawerHtml(r);
    if (actionKey) requestAnimationFrame(() => panel.querySelector(`[data-oc-drawer-action="${actionKey}"]`)?.focus({preventScroll:true}));
  }

  function closeDrawer() {
    $("ocDrawer").classList.remove("open");
    state.selectedStrike = null;
    state.drawerMode = null;
    state.drawerSignature = null;
    document.querySelectorAll(".oc-row.oc-selected").forEach((tr) => tr.classList.remove("oc-selected"));
    const invoker = state.drawerInvoker;
    state.drawerInvoker = null;
    requestAnimationFrame(() => restoreRowInvoker(invoker));
  }

  function openStrikeDetail(strike) {
    const n = Number(strike);
    if (!Number.isFinite(n)) return;
    // The normal D-05 route is window.open() from Dashboard, so opener is
    // the best path: open the Tier-3 report and bring Dashboard forward.
    try {
      if (window.opener && typeof window.opener.openStrikeDetailReportModal === "function") {
        window.opener.openStrikeDetailReportModal(n);
        window.opener.focus();
        closeDrawer();
        return;
      }
    } catch (_) {}
    // Broadcast fallback for a manually opened same-origin D-05 tab.
    if (_ocOrderChan) {
      _ocOrderChan.postMessage({ type:"oc-open-strike-detail", strike:n });
      closeDrawer();
    }
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
        <button data-oc-drawer-action="close" onclick="window.ocCloseDrawer()" aria-label="Close strike detail" style="background:none;border:none;color:var(--text-2);font-size:18px;cursor:pointer;">✕</button>
      </div>
      ${hasDepth
        ? legDepth("CALL", r.ce, "--call") + legDepth("PUT", r.pe, "--put")
        : `<div style="font-size:12px;color:var(--text-3);">No depth data in this feed yet.</div>`}
      <button class="oc-drawer-action" data-oc-drawer-action="detail" onclick="window.ocOpenStrikeDetail(${r.strike})">Open Strike Detail ↗</button>`;
  }

  function buildSummaryDrawerHtml(r) {
    const strike = r.strike;
    return `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
        <div style="font-family:var(--display);font-weight:700;font-size:17px;">${state.symbol} ${strike}${r.isAtm ? ' <span style="color:var(--spine);font-size:11px;">ATM</span>' : ""}</div>
        <button data-oc-drawer-action="close" onclick="window.ocCloseDrawer()" aria-label="Close strike detail" style="background:none;border:none;color:var(--text-2);font-size:18px;cursor:pointer;">✕</button>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div>
          <div style="color:var(--call);font-weight:700;font-size:12px;margin-bottom:8px;">CALL · ${SIGNAL_LABEL[r.ce.signal] || "—"}</div>
          <div style="font-family:var(--mono);font-size:12.5px;line-height:2;color:var(--text-2);">
            LTP <b style="color:var(--text);">${fmtNum(r.ce.ltp)}</b><br>
            IV <b style="color:var(--text);">${fmtPct(r.ce.iv)}</b><br>
            OI <b style="color:var(--text);">${fmt(r.ce.oi)}</b> &nbsp; Chg <b style="color:var(--text);">${sign(r.ce.oiChg)}${fmt(r.ce.oiChg)}</b><br>
            Volume <b style="color:var(--text);">${fmt(r.ce.vol)}</b><br>
            Premium Locked <b style="color:var(--text);">₹${fmt(r.ce.premiumLocked)}</b><br>
            Capital Flow <b style="color:${(r.ce.capitalFlow||0)>=0?'var(--call)':'var(--put)'};">${sign(r.ce.capitalFlow)}₹${fmt(r.ce.capitalFlow)}</b>
          </div>
        </div>
        <div>
          <div style="color:var(--put);font-weight:700;font-size:12px;margin-bottom:8px;">PUT · ${SIGNAL_LABEL[r.pe.signal] || "—"}</div>
          <div style="font-family:var(--mono);font-size:12.5px;line-height:2;color:var(--text-2);">
            LTP <b style="color:var(--text);">${fmtNum(r.pe.ltp)}</b><br>
            IV <b style="color:var(--text);">${fmtPct(r.pe.iv)}</b><br>
            OI <b style="color:var(--text);">${fmt(r.pe.oi)}</b> &nbsp; Chg <b style="color:var(--text);">${sign(r.pe.oiChg)}${fmt(r.pe.oiChg)}</b><br>
            Volume <b style="color:var(--text);">${fmt(r.pe.vol)}</b><br>
            Premium Locked <b style="color:var(--text);">₹${fmt(r.pe.premiumLocked)}</b><br>
            Capital Flow <b style="color:${(r.pe.capitalFlow||0)>=0?'var(--call)':'var(--put)'};">${sign(r.pe.capitalFlow)}₹${fmt(r.pe.capitalFlow)}</b>
          </div>
        </div>
      </div>
      <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--hairline);font-family:var(--mono);font-size:12px;color:var(--text-2);">
        PCR <b style="color:var(--spine);">${r.pcr}</b> (${r.pcrChg}) — put OI share ${((r.pe.oi/(r.pe.oi+r.ce.oi||1))*100).toFixed(0)}% of this strike<br>
        Capital PCR <b style="color:var(--spine);">${((r.pe.premiumLocked||0)/((r.ce.premiumLocked||0)||1)).toFixed(2)}</b> — put premium share ${(((r.pe.premiumLocked||0)/((r.pe.premiumLocked||0)+(r.ce.premiumLocked||0)||1))*100).toFixed(0)}% of this strike's locked capital
      </div>
      <button class="oc-drawer-action" data-oc-drawer-action="detail" onclick="window.ocOpenStrikeDetail(${strike})">Open Strike Detail ↗</button>`;
  }

  function renderExpiryPendingState() {
    const sel = $("ocExpiry");
    if (!sel) return;
    const pending = !!state.requestedExpiry;
    sel.disabled = pending;
    sel.setAttribute("aria-busy", pending ? "true" : "false");
    sel.classList.toggle("is-pending", pending);
    sel.title = pending ? `Loading ${state.requestedExpiry}…` : "";
    if (pending && state.expiry) sel.value = state.expiry;
  }

  function clearExpiryRequest() {
    state.requestedExpiry = null;
    clearTimeout(state._expiryRequestTimer);
    renderExpiryPendingState();
  }

  function trapFocus(container, e) {
    if (e.key !== "Tab") return;
    const focusable = Array.from(container.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'))
      .filter((el) => el.offsetParent !== null);
    if (!focusable.length) { e.preventDefault(); container.focus(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  // ── EVENTS ──
  function wireEvents() {
    $("ocBack").addEventListener("click", () => {
      if (window.opener) { window.close(); }
      // DashboardPro.html lives in its own sibling folder (Dashboard/),
      // not directly in the shared root, now that this page moved into
      // OptionChain/ — was a same-folder "DashboardPro.html" before.
      else { history.length > 1 ? history.back() : (location.href = "../Dashboard/DashboardPro.html"); }
    });

    $("ocExpiry").addEventListener("change", (e) => {
      const requested = e.target.value;
      if (!requested || requested === state.expiry) return;
      if (!_ocRequestExpiry) { e.target.value = state.expiry; return; }
      state.requestedExpiry = requested;
      renderExpiryPendingState();
      _ocRequestExpiry(requested);
      clearTimeout(state._expiryRequestTimer);
      state._expiryRequestTimer = setTimeout(() => clearExpiryRequest(), 8000);
    });

    $("ocRangeGroup").addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      state.range = +btn.dataset.val;
      $("ocRangeGroup").dataset.active = state.range;
      $("ocRangeGroup").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      renderSummary();
      renderRows();
      if (_ocRequestRange) _ocRequestRange(state.range);
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
    $("ocBody").addEventListener("keydown", (e) => {
      if (e.target.closest("button")) return;
      const tr = e.target.closest(".oc-row");
      if (tr && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        openDrawer(+tr.dataset.strike, "summary");
      }
    });

    $("ocDrawer").addEventListener("click", (e) => {
      if (e.target.id === "ocDrawer") closeDrawer();
    });

    $("ocTradeModal").addEventListener("click", (e) => {
      if (e.target.id === "ocTradeModal") closeTradeModal();
    });
    // Close on click anywhere outside the trade panel, or on Escape —
    // the LTP cells that open this modal already call
    // event.stopPropagation() on their own click, so that opening click
    // never reaches this listener and can't immediately close what it
    // just opened.
    document.addEventListener("click", (e) => {
      const modal = $("ocTradeModal");
      if (modal.classList.contains("open") && !$("ocTradePanel").contains(e.target)) {
        closeTradeModal();
      }
    });
    document.addEventListener("keydown", (e) => {
      if ($("ocTradeModal").classList.contains("open")) {
        if (e.key === "Escape") { closeTradeModal(); return; }
        trapFocus($("ocTradePanel"), e);
      } else if ($("ocDrawer").classList.contains("open")) {
        if (e.key === "Escape") { closeDrawer(); return; }
        trapFocus($("ocDrawerPanel"), e);
      }
    });

    // set initial toggle button active state
    $("ocRangeGroup").querySelector(`button[data-val="${state.range}"]`)?.classList.add("active");
  }

  // ── BUY/SELL QUICK-ORDER MODAL (LTP click) ──
  function openTradeModal(strike, side, ltp) {
    const r = state.rows.find((x) => x.strike === strike);
    if (!r) return;
    const leg = side === "CE" ? r.ce : r.pe;
    const colorVar = side === "CE" ? "--ce" : "--pe";
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
    state.tradeInvoker = captureRowInvoker(strike);
    $("ocTradeModal").classList.add("open");
    requestAnimationFrame(() => $("ocTradeQty")?.focus());
  }

  function closeTradeModal() {
    $("ocTradeModal").classList.remove("open");
    const invoker = state.tradeInvoker;
    state.tradeInvoker = null;
    requestAnimationFrame(() => restoreRowInvoker(invoker));
  }

  function placeOrder(action) {
    const ctx = state._tradeCtx;
    if (!ctx) return;
    const qty = Math.max(1, +($("ocTradeQty")?.value || 1));
    const orderPayload = { symbol: state.symbol, expiry: state.expiry, ...ctx, qty, action };
    const el = $("ocTradeConfirm");
    state._tradeCtx = null;

    if (DEMO_MODE || state.feedState !== "LIVE") {
      if (el) {
        el.textContent = DEMO_MODE ? "DEMO ONLY — order not sent" : `${state.feedState} market data — order not sent`;
        el.classList.add("oc-trade-confirm-err", "show");
      }
      setTimeout(closeTradeModal, 1400);
      return;
    }

    // Path 1: embedded in the dashboard's iframe (see panels-views.js's
    // toggleFullChainFocus()) — direct call into the real paper-trading
    // engine. No fill/reject comes back through this call itself (the
    // dashboard's own Order/Trade Log updates independently over its
    // existing WS connection), so this stays an optimistic "sent" message,
    // same as before.
    if (window._ocPlaceOrder) {
      window._ocPlaceOrder(orderPayload);
      if (el) {
        el.textContent = `${action === "BUY" ? "Bought" : "Sold"} ${qty} lot${qty > 1 ? "s" : ""} of ${state.symbol} ${ctx.strike} ${ctx.side} @ ${fmtNum(ctx.ltp)} — sent`;
        el.classList.remove("oc-trade-confirm-err");
        el.classList.add("show");
      }
      setTimeout(closeTradeModal, 900);
      return;
    }

    // Path 2: opened standalone (its own tab/window, no parent to call
    // into) — route the request over the same "oc-live-sync"
    // BroadcastChannel already used for live chain data, and wait for a
    // real {type:"oc-order-result"} reply (see handleOrderResult()) before
    // showing anything. Requires chain-sync.js on the dashboard side to
    // listen for {type:"oc-place-order", reqId, order} and post back
    // {type:"oc-order-result", reqId, status, fill_price|reason} — same
    // symbol/expiry/strike/side/qty shape ptDispatchOrder() already takes
    // elsewhere (side here is CE/PE and action is BUY/SELL, matching
    // panels-views.js's translation into instrument_type/side). With no
    // dashboard tab open to answer, this times out after 8s instead of
    // hanging forever with "Sending…" shown.
    if (_ocOrderChan) {
      const reqId = "oc_" + Date.now() + "_" + Math.random().toString(36).slice(2);
      state._pendingOrderReqId = reqId;
      _ocOrderChan.postMessage({ type: "oc-place-order", reqId, order: orderPayload });
      if (el) {
        el.textContent = "Sending…";
        el.classList.remove("oc-trade-confirm-err");
        el.classList.add("show");
      }
      setTimeout(() => {
        if (state._pendingOrderReqId === reqId) {
          state._pendingOrderReqId = null;
          if (el) {
            el.textContent = "No response — is the dashboard tab open?";
            el.classList.add("oc-trade-confirm-err");
          }
          setTimeout(closeTradeModal, 1500);
        }
      }, 8000);
      return;
    }

    // Path 3: no live order route at all. Never fabricate a trade or a
    // fill in demo/offline mode.
    if (el) {
      el.textContent = DEMO_MODE ? "DEMO ONLY — order not sent" : "DISCONNECTED — order not sent";
      el.classList.add("oc-trade-confirm-err", "show");
    }
    setTimeout(closeTradeModal, 1400);
  }

  // Reply to a request THIS tab sent via the BroadcastChannel path above.
  // Ignored if it doesn't match the reqId we're currently waiting on (a
  // stale reply arriving after the 8s timeout already fired, or a reply
  // meant for some other tab sharing the same channel).
  function handleOrderResult(data) {
    if (!data.reqId || data.reqId !== state._pendingOrderReqId) return;
    state._pendingOrderReqId = null;
    const el = $("ocTradeConfirm");
    if (!el) return;
    if (data.status === "SENT" || data.status === "PENDING") {
      el.textContent = "Order sent — awaiting confirmation";
      el.classList.remove("oc-trade-confirm-err");
    } else if (data.status === "CONFIRMATION_REQUIRED") {
      el.textContent = "Confirmation required on Dashboard — not sent yet";
      el.classList.remove("oc-trade-confirm-err");
    } else if (data.status === "FILLED") {
      // Reserved for a real broker/paper-engine confirmation event only.
      el.textContent = data.fill_price != null ? `Filled @ ${fmtNum(data.fill_price)}` : "Filled";
      el.classList.remove("oc-trade-confirm-err");
    } else {
      el.textContent = `REJECTED: ${data.reason || "order not placed"}`;
      el.classList.add("oc-trade-confirm-err");
    }
    setTimeout(closeTradeModal, 1200);
  }

  // exposed for inline onclick handlers in row/strike/LTP cells and modal buttons
  window.ocOpenTradeModal = openTradeModal;
  window.ocCloseTradeModal = closeTradeModal;
  window.ocPlaceOrder = placeOrder;
  window.ocOpenDepth = (strike) => openDrawer(strike, "depth");
  window.ocCloseDrawer = closeDrawer;
  window.ocOpenStrikeDetail = openStrikeDetail;

  // ── LIVE DATA INTEGRATION ──
  function applyLivePayload(msg) {
    if (!msg || !Array.isArray(msg.rows)) return;
    state.lastLiveAt = Date.now();
    state.feedState = "LIVE";
    const nextSymbol = msg.symbol || state.symbol;
    const nextExpiry = msg.expiry || state.expiry;
    const nextContextKey = `${nextSymbol}|${nextExpiry}`;
    const contextChanged = nextContextKey !== state.contextKey;
    if (contextChanged) {
      state.needsInitialAtmCenter = true;
      if ($("ocDrawer").classList.contains("open")) closeDrawer();
      if ($("ocTradeModal").classList.contains("open")) closeTradeModal();
    }
    state.contextKey = nextContextKey;
    state.rows = msg.rows;
    if (msg.symbol) state.symbol = msg.symbol;
    if (msg.spot != null) state.spot = msg.spot;
    if (msg.spotChg != null) state.spotChg = msg.spotChg;
    if (msg.spotChgPct != null) state.spotChgPct = msg.spotChgPct;
    if (msg.expiry) {
      state.expiry = msg.expiry;
      if (state.requestedExpiry && msg.expiry === state.requestedExpiry) clearExpiryRequest();
    }
    if (msg.expiryDates) state.expiryDates = msg.expiryDates;
    // Max Pain feeds the shared Market Structure labels. volOiRatios is
    // retained in state for the summary/detail surface, but institutional
    // significance now comes from canonical per-row footprintScore.
    if (msg.volOiRatios) state.volOiRatios = msg.volOiRatios;
    if (msg.maxPain != null) state.maxPain = msg.maxPain;
    // Keep this tab's range in sync with the main dashboard's sidebar
    // toggle — chain-sync.js has always sent this field, but nothing
    // here ever read it, so the two views could silently show different
    // ATM ranges with no indication either was out of sync.
    if (msg.range != null && msg.range !== state.range) {
      state.range = msg.range;
      _syncRangeButtons();
    }
    renderAll();
    renderExpiryPendingState();
    if(state.pendingFocusStrike != null) focusStrike(state.pendingFocusStrike);
    else centerAtmOnce();
  }

  function initLiveSync() {
    // Preferred path: BroadcastChannel from the main dashboard tab.
    if ("BroadcastChannel" in window) {
      const chan = new BroadcastChannel("oc-live-sync");
      chan.addEventListener("message", (e) => {
        const data = e.data;
        // {type:"oc-order-result"} is a reply to a request THIS tab sent
        // via placeOrder() below, not a chain-data snapshot — route it to
        // handleOrderResult() instead of applyLivePayload(), which would
        // otherwise just silently drop it (no `rows` field).
        if (data && data.type === "oc-order-result") { handleOrderResult(data); return; }
        if (data && data.type === "oc-focus-strike") { focusStrike(data.strike); return; }
        applyLivePayload(data);
      });
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
      _ocRequestRange = (range) => {
        chan.postMessage({ type: "oc-request-range", range });
      };
      _ocOrderChan = chan;
    }
    // Fallback path: this page was opened via window.open() from the
    // dashboard, which posts messages directly to us.
    window.addEventListener("message", (e) => {
      if (e.data && e.data.rows) applyLivePayload(e.data);
    });
  }

  // ── BOOT ──
  function boot() {
    if (DEMO_MODE) {
      state.rows = buildDemoRows();
      state.spot = 24062.40;
      state.spotChg = 118.30;
      state.spotChgPct = 0.49;
      state.expiry = "24-JUL-2026";
      state.expiryDates = ["24-JUL-2026", "31-JUL-2026", "07-AUG-2026"];
      state.contextKey = `${state.symbol}|${state.expiry}`;
      state.needsInitialAtmCenter = true;
    }
    const hashMatch = location.hash.match(/(?:^#|&)strike=([^&]+)/);
    if(hashMatch){
      const target = Number(decodeURIComponent(hashMatch[1]));
      if(Number.isFinite(target)) state.pendingFocusStrike = target;
    }
    wireEvents();
    renderAll();
    initLiveSync();
    startFeedMonitor();
    if(state.pendingFocusStrike != null) focusStrike(state.pendingFocusStrike);
    else centerAtmOnce();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();