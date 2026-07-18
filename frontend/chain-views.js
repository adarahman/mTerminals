// Tracks whether the per-strike Greeks rows are expanded. Toggled by
// ChainView.toggleGreeks(); read on every render (renderDashboard,
// ChainDenseView.refreshView, switchChainRange) to keep expanded rows
// visible across re-renders instead of resetting to collapsed.
let _greeksVisible = false;

class ChainDenseView {
  constructor() {
    this.currentRange = "3";
    this.velocityWindowMin = 5;
    this.prevSnapshot = {};
    // Previously ad-hoc window._lastPayload/_lastRows/_lastGreeks globals —
    // moved onto the instance so this view's state lives in one place,
    // same as every other panel's app.* state. Cross-class readers (e.g.
    // ChainView.switchChainRange/switchVelTab below) now read
    // app.chainDense.lastX instead of window._lastX.
    this.lastPayload = null;
    this.lastRows = null;
    this.lastGreeks = [];
    this._initBroadcast();
  }

  // ── BROADCAST SYNC to the standalone option-chain.html tab ──
  // option-chain.js already listens on a BroadcastChannel('oc-live-sync')
  // and posts {type:'oc-request-snapshot'} on load and
  // {type:'oc-request-expiry', expiry} when its dropdown changes — but
  // nothing on this side ever opened that channel, so both messages went
  // nowhere: the tab stayed on demo data and its expiry dropdown looked
  // inert. This opens the same channel and answers both message types.
  _initBroadcast() {
    if (!("BroadcastChannel" in window)) return;
    const chan = new BroadcastChannel("oc-live-sync");
    window._ocChan = chan;
    chan.addEventListener("message", (e) => {
      const msg = e.data;
      if (!msg) return;
      if (msg.type === "oc-request-snapshot") {
        if (this.lastPayload) this._broadcastToOptionChainTab(this.lastPayload);
      } else if (msg.type === "oc-request-expiry") {
        // Drive the switch through the real expiry-change path (same one
        // the global #expirySelect uses) — it updates _data and then
        // itself calls refreshView(), which re-broadcasts below.
        if (window.onExpiryChange) window.onExpiryChange(msg.expiry);
      }
    });
  }

  _broadcastToOptionChainTab(payload) {
    if (!window._ocChan) return;
    window._ocChan.postMessage({
      rows: this.lastRows, symbol: payload.symbol, spot: payload.spot,
      spotChg: payload.spotChg, spotChgPct: payload.spotChgPct,
      expiry: payload.expiry, expiryDates: payload.expiryDates,
    });
  }

  setStatus(live, text) {
    const dot = document.getElementById("statusDot");
    if (dot) dot.classList.toggle("live", live);
    const t = document.getElementById("statusText");
    if (t) t.textContent = text;
  }

  mapPayloadToRows(payload) {
    const chainArr = payload.chain || [];
    const expiryKey = payload.expiry || "";
    const velLookup = buildVelocityLookup(payload);
    // Resolved ATM strike, same helper used everywhere else in this file
    // (IV surface, GEX table, etc.) to fall back past a possibly-unset
    // per-row `.atm` flag — see activeAtm() in dashboard.js for the
    // fallback chain (payload.atm -> row.atm -> row.atmStrike -> nearest
    // strike to spot). Without this, isAtm below was `!!row.atm` only,
    // which is false for every row whenever the backend doesn't stamp
    // that flag on the chain rows themselves.
    const atmStrike = (typeof activeAtm === "function") ? activeAtm(payload) : payload.atm;

    let totalCeOi = 0, totalPeOi = 0;
    chainArr.forEach((r) => {
      totalCeOi += r.ceOI || 0;
      totalPeOi += r.peOI || 0;
    });

    const rows = chainArr.map((row) => {
      const rowKey = expiryKey + "_" + row.strike;
      const vel = velLookup[row.strike] || {};
      const prev = this.prevSnapshot[rowKey] || {};

      const ceIvChg = prev.ceIV != null && row.ceIV != null ? +(row.ceIV - prev.ceIV).toFixed(2) : null;
      const peIvChg = prev.peIV != null && row.peIV != null ? +(row.peIV - prev.peIV).toFixed(2) : null;
      const ceVolVel = prev.ceVol != null && row.ceVol != null ? row.ceVol - prev.ceVol : null;
      const peVolVel = prev.peVol != null && row.peVol != null ? row.peVol - prev.peVol : null;
      const ceLtpChg = row.ceChg != null ? row.ceChg : null;
      const peLtpChg = row.peChg != null ? row.peChg : null;

      const ce = {
        iv: row.ceIV, ivChg: ceIvChg, vol: row.ceVol, volChg: row.ceVolChg,
        volPct: row.ceVol ? (((row.ceOI || 0) / row.ceVol) * 100).toFixed(1) : null,
        ltp: row.ceLTP, chg: ceLtpChg, oi: row.ceOI, oiChg: row.ceChgOI,
        oiVel: vel.ceVel, volVel: ceVolVel, signal: row.ceSignal,
        bid: row.ceBid, bidQty: row.ceBidQty, ask: row.ceAsk, askQty: row.ceAskQty,
        totalBidQty: row.ceTotalBidQty, totalAskQty: row.ceTotalAskQty,
      };
      const pe = {
        iv: row.peIV, ivChg: peIvChg, vol: row.peVol, volChg: row.peVolChg,
        volPct: row.peVol ? (((row.peOI || 0) / row.peVol) * 100).toFixed(1) : null,
        ltp: row.peLTP, chg: peLtpChg, oi: row.peOI, oiChg: row.peChgOI,
        oiVel: vel.peVel, volVel: peVolVel, signal: row.peSignal,
        bid: row.peBid, bidQty: row.peBidQty, ask: row.peAsk, askQty: row.peAskQty,
        totalBidQty: row.peTotalBidQty, totalAskQty: row.peTotalAskQty,
      };

      let pcr = ce.oi && pe.oi ? pe.oi / Math.max(ce.oi, 1) : null;
      let pcrChg = null;
      if (ce.oi != null && pe.oi != null && ce.oiChg != null && pe.oiChg != null) {
        const prevCe = ce.oi - ce.oiChg;
        const prevPe = pe.oi - pe.oiChg;
        pcrChg = (pcr != null && prevCe > 0 && prevPe > 0)
          ? pcr - (prevPe / prevCe)
          : null;
      }

      return {
        strike: row.strike, isAtm: !!row.atm || row.strike === atmStrike,
        pcr: pcr != null ? pcr.toFixed(2) : "—",
        pcrChg: pcrChg != null ? sign(pcrChg.toFixed(2)) : "—",
        ce, pe, totalCeOi, totalPeOi,
      };
    });

    const newSnapshot = Object.assign({}, this.prevSnapshot);
    chainArr.forEach((r) => {
      newSnapshot[expiryKey + "_" + r.strike] = { ceIV: r.ceIV, peIV: r.peIV, ceVol: r.ceVol, peVol: r.peVol };
    });
    this.prevSnapshot = newSnapshot;

    return rows.sort((a, b) => a.strike - b.strike);
  }

  buildStrikeDetailHtml(r, g) {
    const hasGreeks = g.cDelta != null;
    // NOTE: LTP/IV/OI/OI-Velocity/Volume are already visible in the
    // collapsed row directly above this panel (and PCR/Combined Signal in
    // the strike cell + rightmost column), so repeating them here just
    // made every expanded strike look identical at a glance. This panel
    // now only shows what ISN'T already on screen: Bid/Ask depth, the
    // per-leg Greeks, Net GEX, and the per-leg (CE/PE) Signal.
    const legBlock = (leg, side) => {
      const color = side === 'ce' ? 'var(--ce)' : 'var(--pe)';
      const delta = side === 'ce' ? g.cDelta : g.pDelta;
      const gamma = side === 'ce' ? g.cGamma : g.pGamma;
      const theta = side === 'ce' ? g.cTheta : g.pTheta;
      const vega  = side === 'ce' ? g.cVega  : g.pVega;
      const bidStr = leg.bid != null ? fmtN(leg.bid, 2) + (leg.bidQty ? ' ×' + fmt(leg.bidQty) : '') : '—';
      const askStr = leg.ask != null ? fmtN(leg.ask, 2) + (leg.askQty ? ' ×' + fmt(leg.askQty) : '') : '—';
      return `
        <div style="min-width:230px;">
          <div style="font-weight:700;color:${color};margin-bottom:4px;">${side.toUpperCase()}</div>
          <div>Bid <strong>${bidStr}</strong> &nbsp;/&nbsp; Ask <strong>${askStr}</strong></div>
          ${hasGreeks ? `<div>&Delta; <strong>${fmtN(delta, 3)}</strong> &nbsp;&Gamma;&times;10&#8308; <strong>${fmtN(gamma, 3)}</strong> &nbsp;&Theta; <strong>${fmtN(theta, 2)}</strong> &nbsp;Vega <strong>${fmtN(vega, 2)}</strong></div>` : ''}
          <div>Signal <strong class="sp ${spClass(leg.signal)}">${leg.signal || '—'}</strong></div>
        </div>`;
    };
    return `
      <div style="display:flex;gap:28px;flex-wrap:wrap;align-items:flex-start;padding:8px 12px;font-size:10.5px;color:var(--text-faint);line-height:1.6;">
        ${legBlock(r.ce, 'ce')}
        <div style="min-width:140px;">
          <div style="font-weight:700;color:var(--oc-amber);margin-bottom:4px;">STRIKE ${r.strike}</div>
          ${hasGreeks ? `<div>Net GEX <strong style="color:${(g.netGEX || 0) >= 0 ? 'var(--ce)' : 'var(--pe)'};">${fmtN(g.netGEX, 3)}B</strong></div>` : ''}
        </div>
        ${legBlock(r.pe, 'pe')}
      </div>`;
  }

  buildRowsHtml(rows) {
    const tbody = document.getElementById("tbody");
    if (!tbody) return; // dense chain markup not on this page — no-op
    const maxOI = Math.max(1, ...rows.map((r) => Math.max(r.ce.oi || 0, r.pe.oi || 0)));
    // Per-strike Greeks lookup, kept in sync by refreshView()/selectDepthStrike()
    // via window._lastGreeks — same payload shape the mini chain panel uses.
    const greeksByStrike = {};
    (window._lastGreeks || []).forEach((g) => { greeksByStrike[g.strike] = g; });
    let html = "";
    rows.forEach((r) => {
      const oiFillCE = (((r.ce.oi || 0) / maxOI) * 100).toFixed(0);
      const oiFillPE = (((r.pe.oi || 0) / maxOI) * 100).toFixed(0);
      const isDepthSelected = selectedDepthStrike === r.strike;
      const g = greeksByStrike[r.strike] || {};
      html += `<tr class="${r.isAtm ? "atm" : ""}${isDepthSelected ? " depth-selected" : ""}"${r.isAtm ? ' id="chain-row-atm"' : ""} style="cursor:pointer;" onclick="toggleGreekRow(${r.strike})" title="Click for full strike summary">`;
      html += `<td>${cell(r.ce.iv != null ? r.ce.iv + "%" : "—", sign(r.ce.ivChg), "flat", dirClass(r.ce.ivChg))}</td>`;
      html += `<td>${cell(fmt(r.ce.vol), r.ce.volPct != null ? r.ce.volPct + "% oi" : "—", "flat", "flat")}</td>`;
      html += `<td class="pt-ltp-click" onclick="event.stopPropagation();ptOpenQuickOrder(event,${r.strike},'CE',${r.ce.ltp!=null?r.ce.ltp:'null'})" title="Click to trade this strike">${cell(r.ce.ltp != null ? r.ce.ltp : "—", sign(r.ce.chg), dirClass(r.ce.chg), dirClass(r.ce.chg))}</td>`;
      html += `<td>${cell(sign(r.ce.oiVel != null ? fmt(r.ce.oiVel) : null), r.totalCeOi ? ((r.ce.oi / r.totalCeOi) * 100).toFixed(1) + "% oi" : "—", dirClass(r.ce.oiVel), "flat")}</td>`;
      html += `<td class="oi-bar"><div class="fill ce" style="width:${oiFillCE}%"></div>${cell(fmt(r.ce.oi), sign(r.ce.oiChg != null ? fmt(r.ce.oiChg) : null), "flat", dirClass(r.ce.oiChg))}</td>`;
      html += `<td class="strike" title="Click to pin Bid/Ask Depth — summary also shown below" onclick="event.stopPropagation();selectDepthStrike(${r.strike});toggleGreekRow(${r.strike})">${cell(r.strike, r.pcr + " / " + r.pcrChg, "", dirClass(parseFloat(r.pcrChg)))}</td>`;
      html += `<td class="oi-bar"><div class="fill pe" style="width:${oiFillPE}%"></div>${cell(fmt(r.pe.oi), sign(r.pe.oiChg != null ? fmt(r.pe.oiChg) : null), "flat", dirClass(r.pe.oiChg))}</td>`;
      html += `<td>${cell(sign(r.pe.oiVel != null ? fmt(r.pe.oiVel) : null), r.totalPeOi ? ((r.pe.oi / r.totalPeOi) * 100).toFixed(1) + "% oi" : "—", dirClass(r.pe.oiVel), "flat")}</td>`;
      html += `<td class="pt-ltp-click" onclick="event.stopPropagation();ptOpenQuickOrder(event,${r.strike},'PE',${r.pe.ltp!=null?r.pe.ltp:'null'})" title="Click to trade this strike">${cell(r.pe.ltp != null ? r.pe.ltp : "—", sign(r.pe.chg), dirClass(r.pe.chg), dirClass(r.pe.chg))}</td>`;
      html += `<td>${cell(fmt(r.pe.vol), r.pe.volPct != null ? r.pe.volPct + "% oi" : "—", "flat", "flat")}</td>`;
      html += `<td>${cell(r.pe.iv != null ? r.pe.iv + "%" : "—", sign(r.pe.ivChg), "flat", dirClass(r.pe.ivChg))}</td>`;
      const cs = chainCombinedSignal(r.ce.signal, r.pe.signal);
      html += `<td class="sig-col"><span class="sig ${cs.cls}">${cs.label}</span></td>`;
      html += `</tr>`;
      // Full per-strike summary row — hidden until the row is clicked
      // (toggleGreekRow). Used to only show Greeks (Delta/Gamma/Theta/Vega);
      // now shows everything available for the strike: LTP/chg, IV/chg,
      // OI/chg, OI velocity, volume/%OI, bid/ask depth, Greeks, per-leg
      // signal, PCR/PCRchg, combined signal, and net GEX. Uses two <tr>
      // elements instead of native <details>/<summary>, because <summary>
      // is not a valid child of <tr>/<tbody>; browsers silently hoist it
      // out of the table and the click handler never fires where expected.
      html += `<tr id="grk-row-${r.strike}" class="grk-row" style="display:none;">
        <td colspan="12" style="text-align:left;padding:0;">${this.buildStrikeDetailHtml(r, g)}</td>
      </tr>`;
    });
    tbody.innerHTML = html;
  }

  filterRowsByRange(rows) {
    if (currentRange === "all") return rows;
    const n = parseInt(currentRange, 10);
    const atmIndex = rows.findIndex((r) => r.isAtm);
    if (atmIndex === -1) return rows;
    return rows.slice(Math.max(0, atmIndex - n), atmIndex + n + 1);
  }

  tickFill(clr) {
    return `repeating-linear-gradient(90deg, ${clr} 0px, ${clr} 2px, transparent 2px, transparent 4px)`;
  }

  buildVelocityLookup(payload) {
    const lookup = {};
    const win = (payload.oiVelocity || []).find((w) => w.window === this.velocityWindowMin);
    if (win && win.rows) {
      win.rows.forEach((r) => { lookup[r.strike] = { ceVel: r.ceDOI, peVel: r.peDOI }; });
    }
    return lookup;
  }

  updateHeader(payload) {
    const symbol = payload.symbol || "NIFTY";
    const spot = payload.spot;
    const pcr = payload.totalPCR;
    const maxPain = payload.maxPain;
    let totalCe = 0, totalPe = 0;
    (payload.chain || []).forEach((r) => { totalCe += r.ceOI || 0; totalPe += r.peOI || 0; });

    const expiryLabel = document.getElementById("expiryLabel");
    if (expiryLabel) expiryLabel.textContent = "OPTION CHAIN";
    const h1 = document.querySelector(".head h1");
    if (h1 && h1.firstChild) h1.firstChild.textContent = symbol + " ";
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set("metaSpot", spot != null ? Number(spot).toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "—");
    set("metaDte", payload.dte != null ? payload.dte + "d" : "—");
    set("metaPcr", pcr != null ? Number(pcr).toFixed(2) : "—");
    set("metaMaxPain", maxPain != null ? Number(maxPain).toLocaleString("en-IN") : "—");
    set("metaOiCe", fmt(totalCe));
    set("metaOiPe", fmt(totalPe));
    const ceVelHdr = document.getElementById("hdr-ce-vel");
    const peVelHdr = document.getElementById("hdr-pe-vel");
    if (ceVelHdr) ceVelHdr.textContent = `CE OI VEL (${this.velocityWindowMin}m)`;
    if (peVelHdr) peVelHdr.textContent = `PE OI VEL (${this.velocityWindowMin}m)`;
  }

  renderExpiryOptions(payload) {
    const sel = getExpirySelectNode();
    if (!sel) return;
    const rawDates = payload.expiryDates || [payload.expiry];
    const dates = (typeof sortExpiryDates === "function") ? sortExpiryDates(rawDates) : rawDates;
    const chainStore = payload.chains || {};
    const activeExpiry = payload.expiry || "";
    const key = dates.join("|");
    if (sel.dataset.optionsKey !== key) {
      sel.innerHTML = dates.map((dt) => {
        const hasData = chainStore[dt] ? true : dt === payload.expiry;
        const bullet = hasData ? "● " : "○ ";
        return `<option value="${dt}"${dt === activeExpiry ? " selected" : ""}>${bullet}${dt}</option>`;
      }).join("");
      sel.dataset.optionsKey = key;
    } else if (sel.value !== activeExpiry) {
      sel.value = activeExpiry;
    }
  }

  selectDepthStrike(strike) {
    selectedDepthStrike = selectedDepthStrike === strike ? null : strike;
    if (window._lastRows) {
      const _visRows = filterRowsByRange(window._lastRows);
      buildRowsHtml(_visRows);
      renderRightPanel(_visRows);
      if (_greeksVisible) document.querySelectorAll('[id^="grk-row-"]').forEach((el) => { el.style.display = ""; });
    }
  }

  refreshView(payload) {
    // Everything below this point (expiry options, row mapping, and the
    // BroadcastChannel push to option-chain.html) must run regardless of
    // whether the dense in-dashboard chain table exists on this page — it
    // no longer does on the main dashboard (moved to option-chain.html),
    // but the main dashboard is exactly the page that has to keep
    // computing rows and broadcasting them for that standalone tab to stay
    // live. Only the actual table-DOM writes further down are page-specific.
    window._lastPayload = payload;
    this.lastPayload = payload;
    renderExpiryOptions(payload);
    window._lastRows = mapPayloadToRows(payload);
    this.lastRows = window._lastRows;
    window._lastGreeks = payload.greeks || [];
    this.lastGreeks = window._lastGreeks;
    this._broadcastToOptionChainTab(payload);

    if (!document.getElementById("tbody")) return; // dense chain markup not on this page
    // payload is expected to already reflect the globally-selected expiry —
    // callers (updateDashboard's WS tick handler, and renderDashboard for
    // paste/file loads) run it through applyExpirySelection(payload,
    // _selectedExpiry) first, so there's no separate override step here.
    updateHeader(payload);
    const _visRows = filterRowsByRange(window._lastRows);
    // ── FIXED-HEIGHT CHAIN BOX ──
    // Capture scroll position before the table body is rebuilt below, so a
    // routine WS tick doesn't yank the user back to ATM mid-browse. Only
    // re-center on ATM the first time this table is populated, or when the
    // expiry actually changes — the same "only ~5 (now 7) strikes visible,
    // scroll for the rest" behavior #chain-scroll's CSS already documents.
    const _wrap = $i('chain-scroll');
    const _prevScrollTop = _wrap ? _wrap.scrollTop : null;
    const _expiryChanged = this._lastExpiryKey !== undefined && this._lastExpiryKey !== payload.expiry;
    const _firstRender = this._lastExpiryKey === undefined;
    this._lastExpiryKey = payload.expiry;
    buildRowsHtml(_visRows);
    renderRightPanel(_visRows);
    if (_greeksVisible) document.querySelectorAll('[id^="grk-row-"]').forEach((el) => { el.style.display = ""; });
    if (window.updateGreeksMoneynessChart) window.updateGreeksMoneynessChart(payload);
    if (_firstRender || _expiryChanged) _centerChainOnATM = true;
    requestAnimationFrame(() => app.chain.sizeAndScrollChain(_prevScrollTop));
  }
}

class RightPanelView {
  constructor() {
    this.selectedDepthStrike = null;
  }

  renderRightPanel(rows) {
    const el = document.getElementById("rightPanel");
    if (!el) return;
    if (!rows || !rows.length) { el.innerHTML = ""; return; }

    const totCeOI = rows.reduce((s, r) => s + (r.ce.oi || 0), 0);
    const totPeOI = rows.reduce((s, r) => s + (r.pe.oi || 0), 0);
    const totCeDOI = rows.reduce((s, r) => s + (r.ce.oiChg || 0), 0);
    const totPeDOI = rows.reduce((s, r) => s + (r.pe.oiChg || 0), 0);
    const totCeVel = rows.reduce((s, r) => s + (r.ce.oiVel || 0), 0);
    const totPeVel = rows.reduce((s, r) => s + (r.pe.oiVel || 0), 0);
    const totCeVol = rows.reduce((s, r) => s + (r.ce.vol || 0), 0);
    const totPeVol = rows.reduce((s, r) => s + (r.pe.vol || 0), 0);
    const totCeVolChg = rows.reduce((s, r) => s + (r.ce.volChg || 0), 0);
    const totPeVolChg = rows.reduce((s, r) => s + (r.pe.volChg || 0), 0);
    const totCeVelVol = rows.reduce((s, r) => s + (r.ce.volVel || 0), 0);
    const totPeVelVol = rows.reduce((s, r) => s + (r.pe.volVel || 0), 0);

    const maxOIBox = Math.max(Math.abs(totCeDOI), Math.abs(totPeDOI), Math.abs(totCeVel), Math.abs(totPeVel), 1);
    const maxVolBox = Math.max(Math.abs(totCeVolChg), Math.abs(totPeVolChg), Math.abs(totCeVelVol), Math.abs(totPeVelVol), 1);

    const bullStrikes = rows.filter((r) => {
      const cs = chainCombinedSignal(r.ce.signal, r.pe.signal);
      return cs.cls === "sig-strongbull" || cs.cls === "sig-bull";
    }).length;
    const bearStrikes = rows.filter((r) => {
      const cs = chainCombinedSignal(r.ce.signal, r.pe.signal);
      return cs.cls === "sig-strongbear" || cs.cls === "sig-bear";
    }).length;
    const aggBias = bullStrikes > bearStrikes ? { label: "Bullish", cls: "sig-bull" }
      : bearStrikes > bullStrikes ? { label: "Bearish", cls: "sig-bear" }
      : { label: "Mixed", cls: "sig-mixed" };

    const panelPCR = totCeOI > 0 ? (totPeOI / totCeOI).toFixed(2) : "—";
    const pcrColor = parseFloat(panelPCR) > 1 ? "var(--ce)" : parseFloat(panelPCR) < 0.8 ? "var(--pe)" : "var(--oc-amber)";

    const netOI = totPeOI - totCeOI;
    const netDOI = totPeDOI - totCeDOI;
    const netVel = totPeVel - totCeVel;
    const netAbsMax = Math.max(Math.abs(netOI), Math.abs(netDOI), Math.abs(netVel), 1);
    const arpBarW = (v, max) => Math.max(Math.round((Math.abs(v) / max) * 158), 3);
    const arpClr = (v) => (v >= 0 ? "var(--ce)" : "var(--pe)");
    const ceShare = totCeOI + totPeOI > 0 ? Math.round((totCeOI / (totCeOI + totPeOI)) * 100) : 50;
    const peShare = 100 - ceShare;

    el.innerHTML = `
    <div class="rp-box">
      <div class="arp-row" style="padding-bottom:5px;margin-bottom:4px;border-bottom:1px solid var(--oc-border);">
        <span class="arp-key">Signal</span>
        <div class="arp-val"><span class="sig ${aggBias.cls}" style="font-size:10px;">${aggBias.label}</span><span style="font-size:9px;color:var(--text-faint);margin-left:4px;">${bullStrikes}↑ ${bearStrikes}↓</span></div>
      </div>
      <div class="arp-row"><span class="arp-key">Net OI</span><div class="arp-val"><span class="arp-num" style="color:${arpClr(netOI)};">${netOI >= 0 ? "+" : ""}${fmt(netOI)}</span><div class="arp-bar" style="width:${arpBarW(netOI, netAbsMax)}px;background-image:${tickFill(arpClr(netOI))};"></div></div></div>
      <div class="arp-row"><span class="arp-key">Chg OI</span><div class="arp-val"><span class="arp-num" style="color:${arpClr(netDOI)};">${netDOI >= 0 ? "+" : ""}${fmt(netDOI)}</span><div class="arp-bar" style="width:${arpBarW(netDOI, netAbsMax)}px;background-image:${tickFill(arpClr(netDOI))};"></div></div></div>
      <div class="arp-row"><span class="arp-key">Vel OI</span><div class="arp-val"><span class="arp-num" style="color:${arpClr(netVel)};">${netVel >= 0 ? "+" : ""}${fmt(netVel)}</span><div class="arp-bar" style="width:${arpBarW(netVel, netAbsMax)}px;background-image:${tickFill(arpClr(netVel))};"></div></div></div>
      <div style="padding-top:5px;margin-top:4px;border-top:1px solid var(--oc-border);">
        <div style="margin-bottom:6px;">
          <div style="display:flex;justify-content:space-between;font-size:8px;font-family:var(--mono);margin-bottom:2px;padding:0 3px;">
            <span style="color:var(--ce);">CE ${ceShare}%</span>
            <span style="color:var(--text-faint);font-size:7px;text-transform:uppercase;letter-spacing:.05em;">OI Split</span>
            <span style="color:var(--pe);">PE ${peShare}%</span>
          </div>
          <div class="oi-flow-bar">
            <div class="oi-flow-ce" style="flex:${ceShare};"></div>
            <div class="oi-flow-pe" style="flex:${peShare};"></div>
          </div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;">
          <span class="arp-key">PCR <span style="font-size:8px;font-weight:400;text-transform:none;">(visible)</span></span>
          <span style="font-size:14px;font-weight:700;font-family:var(--mono);color:${pcrColor};">${panelPCR}</span>
        </div>
      </div>
    </div>

    <div class="rp-box">
      <div class="crp-title" style="display:flex;justify-content:space-between;align-items:center;">
        <span>OI Analytics <span style="color:var(--text-faint);font-weight:400;">(${this.velocityWindowMin}m)</span></span>
      </div>
      <div style="display:grid;grid-template-columns:60px 1fr 1fr;gap:2px;margin-bottom:4px;">
        <div></div><div class="crp-head-ce">CE</div><div class="crp-head-pe">PE</div>
      </div>
      <div class="crp-row"><span class="crp-label">OI</span><div class="crp-ce">${fmt(totCeOI)}</div><div class="crp-pe">${fmt(totPeOI)}</div></div>
      <div class="crp-row"><span class="crp-label">Chg OI</span>${rpBar(totCeDOI, maxOIBox, totCeDOI >= 0 ? "var(--pe)" : "var(--ce)")}${rpBar(totPeDOI, maxOIBox, totPeDOI >= 0 ? "var(--ce)" : "var(--pe)")}</div>
      <div class="crp-row"><span class="crp-label">OI Vel</span>${rpBar(totCeVel, maxOIBox, totCeVel >= 0 ? "var(--pe)" : "var(--ce)")}${rpBar(totPeVel, maxOIBox, totPeVel >= 0 ? "var(--ce)" : "var(--pe)")}</div>
    </div>

    <div class="rp-box">
      <div class="crp-title">Volume Analytics <span style="color:var(--text-faint);font-weight:400;">(live)</span></div>
      <div style="display:grid;grid-template-columns:60px 1fr 1fr;gap:2px;margin-bottom:4px;">
        <div></div><div class="crp-head-ce">CE</div><div class="crp-head-pe">PE</div>
      </div>
      <div class="crp-row"><span class="crp-label">Vol</span><div class="crp-ce">${fmt(totCeVol)}</div><div class="crp-pe">${fmt(totPeVol)}</div></div>
      <div class="crp-row"><span class="crp-label">Vol Chg</span>${rpBar(totCeVolChg, maxVolBox, totCeVolChg >= 0 ? "var(--pe)" : "var(--ce)")}${rpBar(totPeVolChg, maxVolBox, totPeVolChg >= 0 ? "var(--ce)" : "var(--pe)")}</div>
      <div class="crp-row"><span class="crp-label">Vol Vel</span>${rpBar(totCeVelVol, maxVolBox, totCeVelVol >= 0 ? "var(--pe)" : "var(--ce)")}${rpBar(totPeVelVol, maxVolBox, totPeVelVol >= 0 ? "var(--ce)" : "var(--pe)")}</div>
    </div>

    ${buildDepthBoxHtml(rows)}`;
  }

  buildDepthBoxHtml(rows) {
    const atmRow = rows.find((r) => r.isAtm) || rows[Math.floor(rows.length / 2)];
    const pinnedRow = selectedDepthStrike != null ? rows.find((r) => r.strike === selectedDepthStrike) : null;
    const depthRow = pinnedRow || atmRow;
    if (!depthRow) return "";

    const isPinned = !!pinnedRow;
    const titleLabel = isPinned ? `Strike ${depthRow.strike}` : `ATM ${depthRow.strike}`;
    const resetLink = isPinned ? ` <span style="color:var(--text-faint);font-weight:400;text-decoration:underline;cursor:pointer;" onclick="selectDepthStrike(${depthRow.strike})">(reset to ATM)</span>` : "";

    const hasDepth = depthRow.ce.bid != null || depthRow.ce.ask != null || depthRow.pe.bid != null || depthRow.pe.ask != null
      || depthRow.ce.totalBidQty != null || depthRow.ce.totalAskQty != null || depthRow.pe.totalBidQty != null || depthRow.pe.totalAskQty != null;

    if (!hasDepth) {
      return `
      <div class="rp-box">
        <div class="crp-title">Bid/Ask Depth <span style="color:var(--text-faint);font-weight:400;">(${titleLabel})</span>${resetLink}</div>
        <div style="font-size:9px;color:var(--text-faint);line-height:1.5;padding:4px 0;">
          No depth data in feed — backend needs to export <code style="font-family:var(--mono);">ceBid/ceAsk/peBid/peAsk</code> (+Qty) and <code style="font-family:var(--mono);">ceTotalBidQty/ceTotalAskQty/peTotalBidQty/peTotalAskQty</code> per strike.
        </div>
      </div>`;
    }

    const ceBidQty = depthRow.ce.bidQty || 0, ceAskQty = depthRow.ce.askQty || 0;
    const peBidQty = depthRow.pe.bidQty || 0, peAskQty = depthRow.pe.askQty || 0;
    const ceTotBid = depthRow.ce.totalBidQty || 0, ceTotAsk = depthRow.ce.totalAskQty || 0;
    const peTotBid = depthRow.pe.totalBidQty || 0, peTotAsk = depthRow.pe.totalAskQty || 0;

    const quoteLine = (label, bidPx, bidQty, askPx, askQty) => `
        <div style="display:flex;justify-content:space-between;font-size:8px;font-family:var(--mono);padding:0 3px;">
          <span style="color:var(--text-faint);font-size:7px;text-transform:uppercase;letter-spacing:.05em;">${label}</span>
          <span><span style="color:var(--ce);">${bidPx != null ? bidPx : "—"}</span><span style="color:var(--text-faint);"> (${fmt(bidQty)})</span> / <span style="color:var(--pe);">${askPx != null ? askPx : "—"}</span><span style="color:var(--text-faint);"> (${fmt(askQty)})</span></span>
        </div>`;

    const depthBar = (totBid, totAsk) => {
      const total = Math.max(totBid + totAsk, 1);
      const bidShare = Math.round((totBid / total) * 100);
      const askShare = 100 - bidShare;
      return `
        <div style="display:flex;justify-content:space-between;font-size:9px;font-family:var(--mono);font-weight:700;margin:2px 0 2px;padding:0 3px;">
          <span style="color:var(--ce);">${fmt(totBid)}</span>
          <span style="color:var(--pe);">${fmt(totAsk)}</span>
        </div>
        <div class="oi-flow-bar">
          <div class="oi-flow-ce" style="flex:${bidShare};"></div>
          <div class="oi-flow-pe" style="flex:${askShare};"></div>
        </div>`;
    };

    const side = (label, bidPx, bidQty, askPx, askQty, totBid, totAsk) => `
      <div style="margin-bottom:8px;">
        ${quoteLine(label, bidPx, bidQty, askPx, askQty)}
        ${depthBar(totBid, totAsk)}
      </div>`;

    return `
    <div class="rp-box">
      <div class="crp-title">Bid/Ask Depth <span style="color:var(--text-faint);font-weight:400;">(${titleLabel})</span>${resetLink}</div>
      ${side("CE", depthRow.ce.bid, ceBidQty, depthRow.ce.ask, ceAskQty, ceTotBid, ceTotAsk)}
      ${side("PE", depthRow.pe.bid, peBidQty, depthRow.pe.ask, peAskQty, peTotBid, peTotAsk)}
    </div>`;
  }

  rpBar(v, max, clr) {
    const w = Math.max(Math.round((Math.abs(v) / max) * 128), 2);
    return `<div class="crp-spark-wrap"><div class="crp-spark" style="width:${w}px;background-image:${tickFill(clr)};"></div><span style="font-size:9px;font-family:var(--mono);color:${clr};">${fmt(v)}</span></div>`;
  }
}

class ChainView {
  constructor() {
    this.velWin = 5;
    this.centerChainOnATM = true;
    this.grkView = 'delta';
    this.chainRange = 3;
    this.greeksVisible = false;
    this.pcrVisible = false;
    this.selStrike = null;
    this.selectedExpiry = null;
    this.expiryViewCache = {};
    // Last spot value actually rendered — compared against the incoming
    // tick each render to pick a flash-up/flash-down class (see
    // renderTopBarHtml). Lives on the instance, not a module-level var,
    // since this class is used as a singleton (app.chain) that persists
    // across ticks even though the DOM node it renders into doesn't.
    this._lastSpot = null;
  }

  // Builds the <option> list for the top-bar symbol picker.
  //
  // d.fnoSymbols — { indices: [...], stocks: [...] } — is sent by the
  // backend (mTerminals_json.py -> smartapi_client.get_fno_underlyings())
  // and covers EVERY NSE/BSE underlying that currently has live F&O
  // contracts, not just the old 6-symbol COMMON_SYMBOLS shortlist. It's
  // only sent on a full snapshot (not every delta tick), so it's cached
  // on the instance the first time it's seen and reused after that.
  //
  // If the currently active symbol isn't in the cached list for some
  // reason (backend hasn't sent fnoSymbols yet, or --symbol was started
  // with something the ScripMaster doesn't recognize), it's prepended to
  // "Indices" so the dropdown always shows the true current value instead
  // of silently falling back to the first option.
  renderSymbolOptions(active, fnoSymbols){
    if (fnoSymbols && (fnoSymbols.indices || fnoSymbols.stocks)) {
      this._fnoSymbolsCache = fnoSymbols;
    }
    const universe = this._fnoSymbolsCache;

    if (!universe) {
      // Fallback while waiting on the first full snapshot: the old
      // hardcoded shortlist plus a manual "Other…" entry.
      const list = COMMON_SYMBOLS.includes(active) ? COMMON_SYMBOLS : [active, ...COMMON_SYMBOLS];
      return list.map(s=>`<option value="${s}"${s===active?' selected':''}>${s}</option>`).join('')
        + `<option value="__other__">Other…</option>`;
    }

    let indices = universe.indices || [];
    const stocks = universe.stocks || [];
    if (!indices.includes(active) && !stocks.includes(active)) indices = [active, ...indices];

    const opt = s => `<option value="${s}"${s===active?' selected':''}>${s}</option>`;
    return `<optgroup label="Indices">${indices.map(opt).join('')}</optgroup>`
      + `<optgroup label="Stocks">${stocks.map(opt).join('')}</optgroup>`;
  }

  renderTopBarHtml(d, isBear){
  if (isBear === undefined) {
    isBear = (d.decision?.bias==='BEARISH')||(d.compositeBias||'').toLowerCase().includes('bear');
  }
  // Flash direction vs the last tick actually rendered — see the
  // .tick-flash-up/-down keyframes in styles.css for why `animation`
  // (not `transition`) is what makes this visible despite the top-bar
  // being a brand-new DOM node every tick (outerHTML rebuild below).
  // Reset the baseline on a symbol switch first — NIFTY (~24,000) vs
  // BANKNIFTY (~51,000) are different scales entirely, comparing across
  // that boundary would flash a huge, meaningless "move" on the first
  // tick of the new symbol.
  if (d.symbol && d.symbol !== this._lastSpotSymbol) {
    this._lastSpot = null;
    this._lastSpotSymbol = d.symbol;
  }
  const spotNum = Number(d.spot);
  let spotFlashCls = '';
  if (this._lastSpot !== null && !isNaN(spotNum) && spotNum !== this._lastSpot) {
    spotFlashCls = spotNum > this._lastSpot ? ' tick-flash-up' : ' tick-flash-down';
  }
  if (!isNaN(spotNum)) this._lastSpot = spotNum;
  return `<div id="sec-topbar" class="top-bar">
    <div class="top-bar-left">
      <!-- Symbol is now a picker, not static text — picking a value calls
           the same switchActiveIndex(sym) the index-ticker pills already
           use (reconnects WS with ?symbol=..., see ws_handler's
           switch_symbol() on the backend), so this single running
           DashboardPro.html instance switches to whatever symbol you pick
           instead of needing a second backend/window per symbol. The
           persistent-node re-parenting trick isn't needed here (unlike
           #expirySelect) since this rebuilds fresh each render anyway and
           doesn't need to preserve mid-edit state between ticks.
           renderSymbolOptions() below fills in the full backend-supplied
           F&O universe (d.fnoSymbols — every NSE/BSE underlying with live
           F&O contracts, grouped Indices/Stocks) plus whatever custom
           symbol is currently active if it isn't already in that list. -->
      <select id="symbolSelect" class="symbol symbol-select" title="Switch active symbol" onchange="onSymbolPicked(this.value)">${this.renderSymbolOptions(d.symbol||'NIFTY', d.fnoSymbols)}</select>
      <span id="topbar-spot" class="spot${isBear?' bearish':''}${spotFlashCls}">${fmtI(d.spot)}</span>
      ${d.spotChgPct!==undefined?`<span id="topbar-badge" class="badge ${d.spotChgPct>=0?'badge-bull':'badge-bear'}">${d.spotChgPct>=0?'▲':'▼'} ${Math.abs(d.spotChgPct).toFixed(2)}% (${d.spotChange>=0?'+':''}${Math.round(d.spotChange||0)})</span>`:''}
      ${renderIndexTicker(d)}
    </div>
    <div class="expiry-strip">
      <!-- Expiry is its own dedicated pill, separate from DTE, and sits
           leftmost in the strip. The same persistent <select> node from
           #expiry-select-holder is re-parented into #expiry-slot on every
           render (see moveExpirySelectIntoTopBar()) rather than rebuilt,
           so its option list and current value survive live ticks. -->
      <div class="expiry-pill">
        <span class="expiry-pill-label">Expiry</span>
        <span id="expiry-slot"></span>
      </div>
      <div class="expiry-divider"></div>
      <div class="expiry-pill">
        <span class="expiry-pill-label">DTE</span>
        <span class="expiry-pill-val dte-val" id="dte-display">${(d.dte||0)}d</span>
      </div>
      <div class="expiry-divider"></div>
      <div class="expiry-pill">
        <span class="expiry-pill-label">As of</span>
        <span class="expiry-pill-val time-val" id="time-display">${d.refreshTime||'--'}</span>
      </div>
      ${this.renderFundPillHtml(d)}
    </div>
  </div>`;
}

  // Always-visible Profit/Fund readout so a square-off decision doesn't
  // require opening the (collapsed-by-default) Paper Trading panel first.
  // ptComputeFundSummary() lives in paper-trading.js, which loads after
  // this file in DashboardPro.html — safe to call here anyway since this
  // only ever runs at render time (a live WS tick), by which point every
  // script tag has already executed. Guarded regardless, in case
  // paper-trading.js is ever removed/reordered or the portfolio feed
  // hasn't arrived yet.
  renderFundPillHtml(d){
    if (typeof window.ptComputeFundSummary !== 'function') return '';
    const fs = window.ptComputeFundSummary(d);
    if (!fs) return '';
    const pnlColor = fs.netPnl >= 0 ? 'var(--green)' : 'var(--red)';
    const warnCls = fs.lowFund ? ' pt-topbar-pill-warn' : '';
    const openPanel = "onclick=\"var p=document.getElementById('pt-panel'); if(p) p.classList.add('open');\"";
    const fundUnavailable = fs.fundSource === 'live-unavailable';
    return `<div class="expiry-divider"></div>
      <div class="expiry-pill pt-topbar-pill${warnCls}" ${openPanel} title="Net P&amp;L${fs.fundSource==='live-real'?' (real, from AngelOne)':fs.isLive?' after charges (paper model — live mode is on)':' after charges'} — click for full Paper Trading detail">
        <span class="expiry-pill-label">P&amp;L${fs.fundSource==='live-unavailable'?' (paper)':''}</span>
        <span class="expiry-pill-val" style="color:${pnlColor}">${fs.netPnl>=0?'+':''}${fmtI(fs.netPnl)}</span>
      </div>
      <div class="expiry-pill pt-topbar-pill${warnCls}" ${openPanel} title="${fundUnavailable?'Live account funds aren\'t wired up yet — see ptComputeFundSummary() in paper-trading.js':fs.fundSource==='live-real'?'Real available margin, from AngelOne rmsLimit()':'Available margin (approx.)'} — click for full Paper Trading detail">
        <span class="expiry-pill-label">Fund</span>
        <span class="expiry-pill-val" style="color:${fundUnavailable?'var(--txt3)':(fs.lowFund?'var(--red)':'var(--txt)')}">${fundUnavailable?'n/a':fmtI(fs.fund)}</span>
      </div>`;
  }

  renderDecisionBoxHtml(d){
    const dec  = d.decision || {};
    const vrd  = dec.verdicts || {};
    const sigs = dec.activeSignals || [];
    const auto = dec.autoStrategy || {};
    const bias = dec.bias || d.compositeBias || '—';
    const str  = dec.biasStrength || '';
    const conf = dec.confidence || 0;
    const act  = dec.action || '—';
    const actType = dec.actionType || '';
    const conflict = dec.conflictFlag || false;

    const biasIsBull = bias === 'BULLISH';
    const biasIsBear = bias === 'BEARISH';
    const biasColor  = biasIsBull ? 'var(--green)' : biasIsBear ? 'var(--red)' : 'var(--amber)';
    const confColor  = conf >= 65 ? 'var(--green)' : conf >= 40 ? 'var(--amber)' : 'var(--red)';

    const sevDot = s => s === 'warn' ? '\u26A0' : s === 'ok' ? '\u2713' : '\u00B7';
    const sevClr = s => s === 'warn' ? 'var(--red)' : s === 'ok' ? 'var(--green)' : 'var(--txt3)';

    // Verdict rows — unique to this panel only (IV Rank shown in ATM Greeks; DTE in top-bar)
    const verdictDefs = [
      { k: 'PCR',      v: vrd.pcr     || '—' },
      { k: 'VIX',      v: vrd.vix     || '—' },
      { k: 'Max Pain', v: vrd.maxPain || '—' },
      { k: 'CE Wall',  v: vrd.ceWall  || '—' },
      { k: 'PE Wall',  v: vrd.peWall  || '—' },
      { k: 'ATM IV',   v: vrd.atmIV ? vrd.atmIV + (vrd.ivRank ? ' · ' + vrd.ivRank.split('—')[0].trim() : '') : '—' },
    ].filter(x => x.v && x.v !== '—');

    // Auto strategy legs — trades routed through the exact same
    // ptExecuteLeg() the Strategy Payoff panel already uses (see
    // execBtn there), so a leg fired from here goes through identical
    // validation/dispatch/toast/portfolio-refresh behavior. autoStrategy
    // doesn't carry its own expiry field today, so resolve the same way
    // ptExecuteStrategy() does: per-leg expiry if present, else the
    // decision box's own expiry, run through ptResolveStrategyExpiry()
    // in case it's a NEAR/FAR label rather than a real date.
    const decSymbol = d.symbol || '';
    const legRows = (auto.legs || []).map(l => {
      const isBuy = l.action === 'BUY';
      const ac    = isBuy ? 'var(--green)' : 'var(--red)';
      const tc    = (l.type||'').toUpperCase() === 'CE' ? 'var(--red)' : 'var(--green)';
      const legLtp = parseFloat(l.ltp) || 0;
      const legExpiryReal = ptResolveStrategyExpiry(l.expiry || auto.expiry || d.expiry || '');
      const execBtn = legLtp > 0 ? `<span onclick="ptExecuteLeg('${decSymbol}','${legExpiryReal}',${l.strike||0},'${(l.type||'').toUpperCase()}','${l.action}',${l.lots||1},${legLtp})"
        title="Execute this leg as a paper order (expiry ${legExpiryReal||'—'})"
        style="cursor:pointer;font-size:9px;font-weight:800;padding:1px 5px;border-radius:4px;
        background:${ac};color:#0b0d12;margin-left:2px;">▶</span>` : '';
      return `<span class="leg-pill ${isBuy?'buy':'sell'}">
        <span style="color:${ac};font-weight:800;">${l.action}</span>
        <span style="color:${tc}">${(l.type||'').toUpperCase()}</span>
        <span style="color:var(--txt)">${fmtI(l.strike||0)}</span>
        ${legLtp>0?`<span style="color:${ac}">₹${fmtN(legLtp,1)}</span>`:''}
        ${execBtn}
      </span>`;
    }).join('');
    // "Execute all legs" now calls a dedicated function that re-reads
    // _data.decision.autoStrategy fresh at click time (see
    // ptExecuteDecisionStrategy() below) instead of baking a long
    // semicolon-chain of ptExecuteLeg(...) calls into one onclick — a
    // chain like that aborts entirely the moment any one call throws
    // (see the BUGFIX note on ptExecuteLeg), which is exactly what was
    // producing "only one leg executes." A plain function call has no
    // such failure mode and is easier to invoke/debug from the console.
    const decHasExecutableLeg = (auto.legs || []).some(l => parseFloat(l.ltp) > 0);

    return `
<div id="sec-decision" style="background:var(--bg1);border:1px solid var(--border);border-left:4px solid ${biasColor};border-radius:var(--radius);margin-bottom:10px;overflow:hidden;">

  <!-- ── HEADER ROW ── -->
  <div style="display:grid;grid-template-columns:auto 1fr auto auto;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--border);flex-wrap:wrap;">
    <div style="display:flex;flex-direction:column;gap:4px;">
      <span style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.09em;">Decision Engine</span>
      <span style="font-size:18px;font-weight:700;padding:4px 14px;border-radius:20px;background:${biasIsBull?'rgba(18,184,134,0.15)':biasIsBear?'rgba(250,82,82,0.15)':'rgba(245,159,0,0.15)'};color:${biasColor};border:1.5px solid ${biasColor};">
        ${bias}${str?' · '+str:''}${conflict?' ⚡':''}
      </span>
      ${d.futSignal && d.futSignal !== bias ? `<span style="font-size:10px;color:var(--txt3);">Fut: <strong style="color:${biasCls(d.futSignal).includes('bull')?'var(--green)':biasCls(d.futSignal).includes('bear')?'var(--red)':'var(--amber)'}">${d.futSignal}</strong></span>` : ''}
    </div>
    <div style="font-size:11px;color:var(--txt2);padding:0 4px;">${act}</div>
    
    <!-- S & R Levels -->
    <div style="min-width:140px;margin-right:8px;border-right:1px solid var(--border);padding-right:12px;">
      <div style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;margin-bottom:4px;">S & R Levels</div>
      ${(()=>{
        const spot = d.spot || 0;
        const r1   = d.ceWall || 0;
        const s1   = d.peWall || 0;
        const step = d.strikeStep || 200;
        const r2   = r1 + step;
        const s2   = s1 - step;
        const dR1 = Math.abs(r1 - spot);
        const dR2 = Math.abs(r2 - spot);
        const dS1 = Math.abs(s1 - spot);
        const dS2 = Math.abs(s2 - spot);
        const maxDist = Math.max(dR1, dR2, dS1, dS2, 1);
        const proxBar = dist => {
          if (!dist || dist === 0) return 50.0;
          return Math.max(6, Math.round((1 - dist / maxDist) * 92));
        };
        const srCell = (lbl, val, d, clr) => {
          const w = proxBar(d);
          return `
            <div style="display:grid;grid-template-columns:16px 1fr 40px;align-items:center;gap:4px;">
              <span style="font-size:10px;font-weight:700;color:${clr};">${lbl}</span>
              <div style="height:6px;background:var(--bg2);border-radius:3px;overflow:hidden;">
                <div style="height:100%;width:${w}%;background:${clr};border-radius:3px;"></div>
              </div>
              <span style="font-size:10px;font-weight:700;font-family:var(--mono);color:${clr};text-align:right;">${fmtI(val)}</span>
            </div>`;
        };
        return `<div style="display:grid;grid-template-columns:1fr 1fr;row-gap:8px;column-gap:4px;">
          ${srCell('R1', r1, dR1, 'var(--red)')}
          ${srCell('S1', s1, dS1, 'var(--green)')}
          ${srCell('R2', r2, dR2, 'var(--red)')}
          ${srCell('S2', s2, dS2, 'var(--green)')}
        </div>`;
      })()}
    </div>
    
    <div style="text-align:right;">
      <div style="font-size:28px;font-weight:700;font-family:var(--mono);color:${confColor};">${conf}<span style="font-size:13px;">%</span></div>
      <div style="font-size:9px;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;">Confidence</div>
    </div>
  </div>

  <!-- ── BODY: 3 columns ── -->
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:0;">

    <!-- Col 1: Active Signals -->
    <div style="padding:12px 14px;border-right:1px solid var(--border);">
      <div style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Active Signals</div>
      ${sigs.length ? sigs.map(s=>`
        <div style="display:flex;align-items:flex-start;gap:5px;padding:3px 0;border-bottom:1px solid var(--border);font-size:10px;line-height:1.4;">
          <span style="color:${sevClr(s.severity)};font-weight:700;flex-shrink:0;">${sevDot(s.severity)}</span>
          <span style="color:${s.severity==='warn'?'var(--txt)':s.severity==='ok'?'var(--txt)':'var(--txt3)'};">${s.text}</span>
        </div>`).join('') : '<div style="font-size:11px;color:var(--txt3);padding:4px 0;">No active signals.</div>'}
    </div>

    <!-- Col 2: Verdicts -->
    <div style="padding:12px 14px;border-right:1px solid var(--border);">
      <div style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Verdicts</div>
      ${verdictDefs.map(r=>`
        <div style="padding:3px 0;border-bottom:1px solid var(--border);">
          <div style="font-size:9px;color:var(--txt3);font-weight:600;text-transform:uppercase;letter-spacing:.05em;">${r.k}</div>
          <div style="font-size:10px;color:var(--txt2);line-height:1.4;">${r.v}</div>
        </div>`).join('')}
    </div>

    <!-- Col 3: Strategy legs -->
    <div style="padding:12px 14px;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:6px;">
        <div style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;">Strategy${auto.name?' — '+auto.name:''}</div>
        ${auto.name ? `<div style="display:flex;align-items:center;gap:6px;">
          <span style="font-size:9px;color:var(--txt3);">Suggested</span>
          ${auto.netPremium!=null?`<span style="font-size:10px;color:${auto.netPremium>=0?'var(--green)':'var(--red)'};font-weight:600;">${auto.netPremium>=0?'Credit':'Debit'} ₹${Math.abs(auto.netPremium).toFixed(1)}</span>`:''}
          ${decHasExecutableLeg ? `<span onclick="ptExecuteDecisionStrategy()" title="Place all legs of this strategy as paper orders"
            style="cursor:pointer;font-size:9px;font-weight:800;padding:2px 8px;border-radius:5px;
            background:var(--accent,#3b82f6);color:#fff;">▶ Execute</span>` : ''}
        </div>` : ''}
      </div>
      ${legRows ? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;">${legRows}</div>` : '<div style="font-size:11px;color:var(--txt3);">No strategy computed.</div>'}
      ${auto.maxProfit!=null||auto.maxLoss!=null ? `<div style="display:flex;gap:10px;font-size:10px;flex-wrap:wrap;padding-top:6px;border-top:1px solid var(--border);">
        ${auto.maxProfit!=null?`<span style="color:var(--green);">Max Profit ₹${fmtI(auto.maxProfit)}</span>`:''}
        ${auto.maxLoss!=null?`<span style="color:var(--red);">Max Loss ₹${fmtI(auto.maxLoss)}</span>`:''}
        ${auto.ivRank!=null?`<span style="color:var(--txt3);">IV Rank ${auto.ivRank}</span>`:''}
      </div>` : ''}
    </div>

  </div>
</div>`;
}

  // ── FIX: patchTopBarAndDecision was called from scheduleRender() on every
  // WS tick but was never actually defined anywhere in this file (only
  // referenced in the comment above it). Since `window.patchTopBarAndDecision`
  // was always undefined, that `if` silently no-op'd on every tick, so the
  // top-bar spot/badge and the whole Decision Engine box only ever got drawn
  // once — inside the full renderDashboard() rebuild — and looked frozen
  // until a manual page refresh forced that rebuild again. This patches both
  // in place using the exact same templates renderDashboard() uses, so they
  // now stay live tick-to-tick without touching/flickering the rest of the DOM.
  //
  // ── DROPDOWN FIX ──
  // The original fix above still did `topBarEl.outerHTML = this.renderTopBarHtml(d)`
  // on every single tick (several times a second). That destroys and rebuilds
  // the whole top-bar subtree each time, including the symbol <select>
  // (regenerated from an HTML string on every render) and the #expiry-slot
  // the persistent #expirySelect node lives in. Even re-parenting a node
  // into a brand-new slot (moveExpirySelectIntoTopBar) forces the browser to
  // close any currently-open native <select> popup, because the element is
  // being moved in the DOM tree. Net effect: neither dropdown could ever
  // stay open longer than the gap between two ticks — a fraction of a
  // second — no matter how fast you clicked.
  //
  // Fix: only do the destructive full rebuild when the symbol actually
  // changes (new option list, new price scale) or on the very first render.
  // On every other tick, patch just the pieces that legitimately change —
  // spot price, %-badge, index ticker, DTE, time — in place. Both <select>
  // elements are left completely untouched on a normal tick, so an open
  // dropdown stays open and clickable across live updates.
  patchTopBarAndDecision(d){
  if (!d) return;
  const topBarEl = document.getElementById('sec-topbar');
  const symbolChanged = !topBarEl || d.symbol !== this._lastTopBarSymbol;
  this._lastTopBarSymbol = d.symbol;

  if (symbolChanged) {
    if (topBarEl) topBarEl.outerHTML = this.renderTopBarHtml(d);
    // The expiry <select> is a persistent node re-parented into the fresh
    // top-bar's #expiry-slot — only needed right after a full rebuild.
    if (window.moveExpirySelectIntoTopBar) moveExpirySelectIntoTopBar();
  } else {
    const isBear = (d.decision?.bias==='BEARISH')||(d.compositeBias||'').toLowerCase().includes('bear');

    const spotNum = Number(d.spot);
    let spotFlashCls = '';
    if (this._lastSpot !== null && !isNaN(spotNum) && spotNum !== this._lastSpot) {
      spotFlashCls = spotNum > this._lastSpot ? 'tick-flash-up' : 'tick-flash-down';
    }
    if (!isNaN(spotNum)) this._lastSpot = spotNum;

    const spotEl = document.getElementById('topbar-spot');
    if (spotEl) {
      spotEl.textContent = fmtI(d.spot);
      // Re-triggering the same animation class needs a reflow in between,
      // or the browser treats it as a no-op and the flash never replays.
      spotEl.className = 'spot' + (isBear ? ' bearish' : '');
      if (spotFlashCls) { void spotEl.offsetWidth; spotEl.classList.add(spotFlashCls); }
    }
    const badgeEl = document.getElementById('topbar-badge');
    if (badgeEl && d.spotChgPct !== undefined) {
      badgeEl.className = 'badge ' + (d.spotChgPct >= 0 ? 'badge-bull' : 'badge-bear');
      badgeEl.textContent = `${d.spotChgPct>=0?'▲':'▼'} ${Math.abs(d.spotChgPct).toFixed(2)}% (${d.spotChange>=0?'+':''}${Math.round(d.spotChange||0)})`;
    }
    const tickerEl = document.getElementById('index-ticker-bar');
    if (tickerEl) {
      const html = renderIndexTicker(d);
      if (tickerEl.outerHTML !== html) tickerEl.outerHTML = html;
    }
    const dteEl = document.getElementById('dte-display');
    if (dteEl) dteEl.textContent = (d.dte||0) + 'd';
    const timeEl = document.getElementById('time-display');
    if (timeEl) timeEl.textContent = d.refreshTime || '--';
  }

  const decEl = document.getElementById('sec-decision');
  if (decEl) decEl.outerHTML = this.renderDecisionBoxHtml(d);
}

  renderDashboard(d){
  _data=d;
  // Reset any expiry-switch overrides when fresh JSON is loaded
  delete _data._activeExpiry; delete _data._overrideAtm;
  delete _data._overrideCeWall; delete _data._overridePeWall;
  delete _data._overrideMaxPain; delete _data._overridePCR;
  delete _data._overrideStraddle; delete _data._overrideAtmIV;
  applyExpirySelection(_data, _selectedExpiry);
  const atm=activeAtm(d);
  const greeksAll=d.greeks||[];
  const straddle=(d.callPremium||0)+(d.putPremium||0);
  
  const chain=getFilteredChain(d);
  const chainStrikeSet=new Set(chain.map(r=>r.strike));
  const greeks=greeksAll.filter(g=>chainStrikeSet.has(g.strike));
  const combinedMode=true;
  
  const maxOI=Math.max(...chain.map(r=>Math.max(r.ceOI||0,r.peOI||0)),1);
  const totalGEX=greeks.reduce((s,g)=>s+(g.netGEX||0),0);
  // Market Story card (renderExecutiveDashboard) reads d.totalGEX directly —
  // it was only ever computed as a local variable here and in renderGEX(),
  // so d.totalGEX was always undefined and the card permanently showed "—".
  d.totalGEX = totalGEX;
  const isBull=(d.decision?.bias==='BULLISH')||(d.compositeBias||'').toLowerCase().includes('bull');
  const isBear=(d.decision?.bias==='BEARISH')||(d.compositeBias||'').toLowerCase().includes('bear');
  const sigs=d.signals||[];
  
  let h='';

  // ── TOP BAR (first) ──
  // Index ticker (fixed order NIFTY/BANKNIFTY/MIDCPNIFTY/SENSEX) is now
  // rendered inline inside renderTopBarHtml() itself, so no separate patch
  // call is needed here.
  h+=this.renderTopBarHtml(d, isBear);

  // ── DECISION ENGINE PANEL ──
  h+=this.renderDecisionBoxHtml(d);

  // ── LARGE EXECUTIVE BOXES (now a 3-col grid: Market Health | Market Story | Top Movers) ──
  h += renderExecutiveDashboard(d);

  // ── OPTIONS CHAIN ──
  // The dense Option Chain table itself lives as a static block outside
  // this template (see #sec-chain in the HTML) so it never gets torn down
  // by a dashboard rebuild — chain-anchor just marks where that block
  // gets moved to (right after the full-rebuild swap below), which is
  // between the Decision/Executive boxes and OI Flow. The duplicate chain
  // table + right analytics panel that used to be generated directly in
  // this template have been removed: the main dense Option Chain table
  // (see ChainDenseView.buildRowsHtml) now has the same click-a-row /
  // "▶ Greeks" toggle-all reveal, and its own #rightPanel
  // (RightPanelView.renderRightPanel) already carries the identical
  // Signal / OI Analytics / Volume Analytics boxes plus a Bid/Ask depth
  // box. velByStrike/velMax below are still needed by the OI Flow panel
  // further down this function.
  h += this.buildChainSummaryHtml(d);
  h += '<div id="chain-anchor"></div>';
  const velBlock=(d.oiVelocity||[]).find(b=>b.window===_velWin)||(d.oiVelocity||[])[0];
  const velByStrike={};
  if(velBlock&&velBlock.rows)velBlock.rows.forEach(vr=>{velByStrike[vr.strike]=vr;});
  const velMax=Math.max(...chain.map(r=>{const vr=velByStrike[r.strike]||{};return Math.max(Math.abs(vr.ceDOI||0),Math.abs(vr.peDOI||0));}),1);

  // OI BUILDUP + GREEKS/GEX (merged) — one 2-column grid. Strikes are the
  // shared x-axis across both panels, so header height, column-label row
  // height, and body-row height are pinned identical across both cards —
  // anything that isn't per-strike data (PCR toggle, biggest-build
  // summary) sits outside that stack so it can't knock rows out of line.
  h+=`<div id="sec-oi-buildup">
    ${buildOiFlowSummaryHtml(chain, atm, velByStrike)}
  </div>`;

  // ── GREEKS SUMMARY — alerts card (gamma flip / short-gamma / theta
  // decay) grouped together with the ATM Greeks numbers, since both are
  // "Greeks" info and read better side by side than split across two
  // unrelated sections. IV Surface (below) is a different data family
  // (per-strike IV skew, not Greeks) so it stays separate. #atm-greeks-card
  // gets its own id so it can be refreshed on an expiry switch the same
  // way #greeks-alerts-card already is — previously it had no id and only
  // ever updated on a full rebuild, going stale between expiry switches.
  h+=`<div id="sec-greeks-summary" class="two-col">
    ${this.buildGreeksAlertsHtml(greeks, atm, d)}
    ${this.buildAtmGreeksHtml(d)}
  </div>`;

  // ── IV SURFACE — alerts-only summary here (elevated skew / IV rank
  // extremes); the full per-strike CE/PE bar table moved into its own
  // modal (openIvSurfaceModal(), same treatment as Greeks/GEX), refreshed
  // by renderIvSurfaceModal() below rather than rebuilt inline here. ──
  h+=`<div id="sec-iv">
    ${this.buildIvAlertsHtml(d, chain, atm)}
  </div>`;
  
  // ── STRATEGIES + SIMULATOR (2-column) ──
  const strats=d.strategies||[];
  if(strats.length){
    // Build dropdown options
    if(_selStratIdx>=strats.length) _selStratIdx=0;
    const stratOpts = strats.map((s,i)=>`<option value="${i}"${i===_selStratIdx?' selected':''}>${s.name||('Strategy '+(i+1))}</option>`).join('');

    // == INSTITUTIONAL F&O SIMULATOR SECTION ==
  // Always inject it - uses live greeks data + simulation sliders
  {
    const simCtx = d.ctx || {};
    const greeksData = d.greeks || [];
    // Prefer the per-expiry fields (d.spot/d.atm/d.atmIV) that applyExpirySelection
    // actually updates on expiry switch — d.ctx is a static top-level payload
    // field that's never touched when the expiry changes, so reading it here
    // pinned the whole simulator to whatever expiry loaded first.
    const spot = d.spot || simCtx.spot || 0;
    const atmStrike = d.atm || simCtx.atm || 0;
    const step = greeksData.length > 1 ? (greeksData[1].strike - greeksData[0].strike) : 50;
    const totalGEX = greeksData.reduce((s,g)=>s+(g.netGEX||0),0);
    const flipRow = findGammaFlipStrike(greeksData);
    const flipStrike = flipRow ? flipRow.strike : 0;
    const vannaMultiplier = 1.0 + Math.abs(totalGEX) / 30;

    // Scenario Controls — single source of truth per slider (id, range,
    // which window-global override var it writes to, and how its value is
    // formatted). Every row is generated from renderSimRangeRow() below
    // instead of being hand-typed three times, so a control that gets
    // added/removed can't drift out of sync with its siblings — e.g. this
    // is what let the Vol/OI Velocity row get dropped from the markup
    // previously while sim-vel-val/sim-vel-slider were still expected
    // elsewhere in panels-views.js.
    const simRangeControls = [
      { id: 'spot', label: 'Spot Price',
        min: Math.round(spot*0.97), max: Math.round(spot*1.03), step: step,
        override: _simSpotOverride, overrideVar: '_simSpotOverride',
        base: spot, clamp: true, fmt: v => fmtI(Math.round(v)) },
      { id: 'iv', label: 'IV (%)',
        min: 8, max: 50, step: 0.5,
        override: _simIvOverride, overrideVar: '_simIvOverride',
        base: d.atmIV || simCtx.baseIv || 15, fmt: v => fmtN(v, 1) },
    ];
    // Kept separate from simRangeControls above: this slider scales the
    // CE/PE Vol/OI Ratio bars in the right-hand panel (see simRenderVolGrid
    // in panels-views.js, which multiplies each ratio by simVel) — it has
    // no effect on the GEX chart/stats that Spot Price and IV drive, so it
    // renders down next to that table instead of grouped with them here.
    const velControl = {
      id: 'vel', label: 'Vol/OI Velocity',
      min: 0.1, max: 5, step: 0.1,
      override: _simVelOverride, overrideVar: '_simVelOverride',
      base: simCtx.baseVel || 1.2, fmt: v => fmtN(v, 1) };

    function renderSimRangeRow(cfg) {
      const raw = cfg.override != null ? parseFloat(cfg.override) : cfg.base;
      const value = cfg.clamp ? Math.min(cfg.max, Math.max(cfg.min, Math.round(raw))) : raw;
      return `
          <div class="sim-ctrl-row">
            <span class="sim-ctrl-label">${cfg.label}</span>
            <input type="range" class="sim-ctrl-slider" id="sim-${cfg.id}-slider" min="${cfg.min}" max="${cfg.max}" step="${cfg.step}" value="${value}" oninput="${cfg.overrideVar}=parseFloat(this.value);simUpdate()">
            <span class="sim-ctrl-val" id="sim-${cfg.id}-val">${cfg.fmt(value)}</span>
          </div>`;
    }

  h+=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;align-items:stretch;">

    <!-- LEFT: Strategy Payoff -->
    <div id="sec-strats" class="section-card" style="min-width:0;min-height:0;overflow:hidden;display:flex;flex-direction:column;">

      <div class="section-header"><span class="section-title">Strategy Payoff</span></div>

      <!-- Dropdowns row -->
      <div style="display:flex;gap:8px;margin-bottom:10px;">
        <select id="strat-select" onchange="_selStratIdx=parseInt(this.value)||0;renderStratPayoff()" style="
          flex:1;padding:10px 14px;font-size:13px;font-weight:600;
          background:var(--bg2);color:var(--txt);
          border:1px solid var(--border);border-radius:8px;
          font-family:var(--sans);cursor:pointer;outline:none;
          appearance:none;-webkit-appearance:none;
          background-image:url('data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'12\\' height=\\'8\\' viewBox=\\'0 0 12 8\\'><path d=\\'M1 1l5 5 5-5\\' stroke=\\'%23868E96\\' stroke-width=\\'1.5\\' fill=\\'none\\' stroke-linecap=\\'round\\'></path></svg>');
          background-repeat:no-repeat;background-position:right 12px center;padding-right:34px;
        ">${stratOpts}</select>
        <select id="strat-strike-select" onchange="_selStrike=this.value?parseFloat(this.value):null;renderStratPayoff()" style="
          flex:1;padding:10px 14px;font-size:13px;font-weight:600;
          background:var(--bg2);color:var(--txt);
          border:1px solid var(--border);border-radius:8px;
          font-family:var(--sans);cursor:pointer;outline:none;
          appearance:none;-webkit-appearance:none;
          background-image:url('data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'12\\' height=\\'8\\' viewBox=\\'0 0 12 8\\'><path d=\\'M1 1l5 5 5-5\\' stroke=\\'%23868E96\\' stroke-width=\\'1.5\\' fill=\\'none\\' stroke-linecap=\\'round\\'></path></svg>');
          background-repeat:no-repeat;background-position:right 12px center;padding-right:34px;
        "><option value="">ATM Strike</option></select>
      </div>

      <!-- Metric cards row -->
      <div id="strat-metrics" style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px;"></div>

      <!-- Payoff chart canvas -->
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px 14px 10px;position:relative;">
        <canvas id="strat-payoff-canvas" style="width:100%;display:block;" height="280"></canvas>
      </div>

      <!-- Leg pills -->
      <div id="strat-legs-row" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;align-items:center;"></div>

    
    </div>

    <!-- RIGHT: Greeks by Moneyness -->
    <div id="sec-greeks-moneyness" class="section-card" style="min-width:0;min-height:0;overflow:hidden;display:flex;flex-direction:column;">
      <div class="section-header"><span class="section-title">Greeks by Moneyness</span></div>
      <div style="display:flex;flex-wrap:wrap;gap:16px;margin-bottom:10px;font-size:11px;color:var(--txt3);flex-shrink:0;">
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#2a78d6;"></span>Delta (call)</span>
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#1baf7a;"></span>Gamma</span>
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#e34948;"></span>|Theta| decay</span>
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#eda100;"></span>Vega</span>
      </div>
      <!-- flex:1 + min-height:0 is the standard fix for Chart.js (responsive +
           maintainAspectRatio:false) inside a flex column: without min-height:0
           the flex item's default min-height:auto fights the canvas's own
           measurement and the box grows/shrinks abruptly on every render. -->
      <div style="position:relative;width:100%;flex:0.9;min-height:280px;">
        <canvas id="greeksChart" role="img" aria-label="Line chart showing how delta, gamma, theta, and vega change shape from deep OTM through ATM to deep ITM for a call option, updated live from the option chain.">Delta rises steadily from OTM to ITM. Gamma, theta decay, and vega all peak at the at-the-money strike and fall off toward both deep ITM and deep OTM.</canvas>
      </div>
    </div>  

  </div>

  <!-- HALF WIDTH ROW: Institutional F&O Simulator (left) + Risk dashboard (right) -->
  <div class="sim-risk-half-row" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;align-items:start;">

  <div id="sec-simulator" class="sim-wrap" style="min-width:0;">

      <div class="sim-header">
        <div class="sim-title">Institutional F&amp;O Simulator</div>
        <div class="sim-subtitle">Net GEX Profile &bull; Vanna Multiplier &bull; Vol/OI Velocity &bull; Dealer Regime</div>
      </div>
      <div class="sim-body" style="padding:10px 14px;">

        <!-- GEX Chart -->
        <div class="sim-chart-area" style="padding-bottom:12px;" id="sim-chart-container">
          <div class="sim-chart-label">Net GEX Profile ($B) &#8593;</div>
          <canvas id="sim-gex-canvas" height="180"></canvas>
          <div class="sim-annot" id="sim-annot"></div>
        </div>

        <!-- Dealer Regime bar — Dealer Bias dropdown sits at the right end
             of this same line (after the regime value), since it's the
             control that drives this readout. -->
        <div class="sim-regime-bar" id="sim-regime-bar">
          <span class="sim-regime-label">Dealer Regime</span>
          <div class="sim-regime-track" id="sim-regime-track"><div class="sim-regime-needle" id="sim-regime-needle" style="left:50%;"></div></div>
          <span class="sim-regime-val" id="sim-regime-val">Balanced</span>
          <select class="sim-dealer-sel" id="sim-dealer-sel" onchange="_simDealerOverride=this.value;simUpdate()" style="flex:none;flex-shrink:0;margin-left:8px;width:12ch;max-width:12ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
            <option value="0"${_simDealerOverride===null||_simDealerOverride==='0'?' selected':''}>Auto</option>
            <option value="1"${_simDealerOverride==='1'?' selected':''}>Long Gamma</option>
            <option value="-1"${_simDealerOverride==='-1'?' selected':''}>Short Gamma</option>
            <option value="0.5"${_simDealerOverride==='0.5'?' selected':''}>Mild Long</option>
            <option value="-0.5"${_simDealerOverride==='-0.5'?' selected':''}>Mild Short</option>
          </select>
        </div>

        <!-- Stats row -->
        <div class="sim-stats-row">
          <div class="sim-stat">
            <div class="sim-stat-label">Net GEX ($B)</div>
            <div class="sim-stat-val" id="sim-stat-gex" style="color:${totalGEX>=0?'var(--blue)':'var(--red)'};">${fmtN(totalGEX,2)}</div>
            <div class="sim-stat-sub">${totalGEX>=0?'Long gamma (dampens)':'Short gamma (amplifies)'}</div>
          </div>
          <div class="sim-stat">
            <div class="sim-stat-label">Vanna Multiplier</div>
            <div class="sim-stat-val" id="sim-stat-vanna" style="color:var(--amber);">${fmtN(vannaMultiplier,2)}</div>
            <div class="sim-stat-sub">IV-flow amplifier</div>
          </div>
          <div class="sim-stat">
            <div class="sim-stat-label">Gamma Flip Strike</div>
            <div class="sim-stat-val" id="sim-stat-flip" style="color:var(--red);">${flipStrike?fmtI(flipStrike):'--'}</div>
            <div class="sim-stat-sub">Short &rarr; Long GEX</div>
          </div>
        </div>

        <!-- Simulation Controls -->
        <div style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Scenario Controls</div>
        <div class="sim-controls" style="grid-template-columns:1fr;">
          ${simRangeControls.map(renderSimRangeRow).join('')}
        </div>

      </div>
    
    </div>

    <!-- RIGHT: Vol/OI Velocity + Strike Detail, sits next to the simulator -->
    <div id="sec-simulator-detail" class="sim-wrap" style="min-width:0;">
      <div class="sim-body" style="padding:14px 14px;">

        <!-- Vol/OI Velocity Breakdown -->
        <div style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Vol/OI Velocity by Strike (Block Detection)</div>
        <div class="sim-controls" style="grid-template-columns:1fr;margin-bottom:10px;">
          ${renderSimRangeRow(velControl)}
        </div>
        <div class="sim-vol-grid" id="sim-vol-grid"></div>

        <!-- Strike Table -->
        <div style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;margin-top:14px;">Strike Detail</div>
        <div class="sim-table-wrap">
          <div style="display:grid;grid-template-columns:70px 1fr 54px 54px 1fr;padding:6px 10px;border-bottom:1px solid var(--border);background:var(--bg1);">
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;">Strike</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;">Open Interest</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;text-align:right;">IV</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;text-align:right;">Net Delta</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;text-align:right;">Institutional Activity</span>
          </div>
          <div id="sim-strike-table"></div>
        </div>

      </div>
    </div>

    </div>
    <!-- close half-width row: Simulator | Vol/OI + Strike Detail -->
  `;
  }
  }


  const risk=d.risk||{};
  const kl=risk.keyLevels||[];
  if(risk.keyLevels||risk.ivRegime||risk.tradeGrade){
    const ivRgClr=risk.ivRegime==='Rich'?'var(--red)':risk.ivRegime==='Cheap'?'var(--green)':'var(--amber)';
    const gradeClr=risk.tradeGrade&&risk.tradeGrade.startsWith('A')?'var(--green)':risk.tradeGrade&&risk.tradeGrade.startsWith('B')?'var(--amber)':'var(--txt3)';
    h+=`<div id="sec-risk" class="section-card" style="margin-bottom:10px;min-width:0;">
      <div class="section-header"><span class="section-title">Risk dashboard</span></div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;margin-bottom:10px;">
        ${risk.tradeGrade&&risk.tradeGrade!=='—'?`<div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;"><div style="font-size:10px;color:var(--txt3);margin-bottom:3px;">Trade grade</div><div style="font-size:18px;font-weight:800;color:${gradeClr};">${risk.tradeGrade}</div></div>`:''}
        ${risk.ivRegime?`<div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;"><div style="font-size:10px;color:var(--txt3);margin-bottom:3px;">IV regime</div><div style="font-size:13px;font-weight:700;color:${ivRgClr};">${risk.ivRegime}</div><div style="font-size:10px;color:var(--txt3);">IV−HV ${risk.ivHvSpread>=0?'+':''}${fmtN(risk.ivHvSpread,2)}%</div></div>`:''}
        ${risk.trapWarn&&risk.trapWarn.toLowerCase()!=='none'?`<div style="background:rgba(250,82,82,0.08);border:1px solid rgba(250,82,82,0.3);border-radius:6px;padding:8px 10px;"><div style="font-size:10px;color:var(--txt3);margin-bottom:3px;">Trap warning</div><div style="font-size:12px;font-weight:700;color:var(--red);">${risk.trapWarn}</div></div>`:''}
      </div>
      ${kl.length?`<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;">
      ${kl.map(k=>{const c=k.cls==='bull'?'var(--green)':k.cls==='bear'?'var(--red)':'var(--txt2)';return `<div style="text-align:center;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:6px 8px;"><div style="font-size:9px;color:var(--txt3);text-transform:uppercase;margin-bottom:2px;">${k.label}</div><div style="font-size:12px;font-weight:700;font-family:var(--mono);color:${c};">${fmtI(k.value)}</div></div>`;}).join('')}
      </div>`:''}
    </div>`;
  }



  // Detach the chart canvases before the full rebuild so their last-drawn
  // frame stays on screen instead of flashing blank while charts redraw.
  const _oldPayoffCanvas = document.getElementById('strat-payoff-canvas');
  const _oldGexCanvas = document.getElementById('sim-gex-canvas');

  // ── FLICKER FIX: preserve the whole Strategy Payoff / Institutional
  // Simulator / Greeks-by-Moneyness subtrees across live ticks ──
  // Every WS tick runs this full rebuild, which was destroying and
  // recreating ALL of their DOM every time — 4 range sliders, 2 <select>
  // dropdowns, and the vol-grid/strike-table, not just the two canvases
  // handled above. That churn is what read as "heavy flicker" on these
  // two sections specifically (native form controls repaint far more
  // noticeably than plain text does). Their actual numbers are already
  // refreshed afterward by renderStratPayoff()/simInit() without touching
  // these nodes, so it's safe to keep the old nodes as-is whenever the
  // strategy list itself hasn't structurally changed (same names/count) —
  // only rebuild them fresh when the strategy list actually changes.
  const dashEl = $i('dashboard');
  const _stratsSig = (d.strategies||[]).map(s=>s.name||'').join('|');
  const _keepInteractiveSubtrees = dashEl && dashEl.dataset.stratsSig === _stratsSig;
  const _oldStratsSection    = _keepInteractiveSubtrees ? document.getElementById('sec-strats') : null;
  const _oldGreeksMoneySect  = _keepInteractiveSubtrees ? document.getElementById('sec-greeks-moneyness') : null;
  const _oldSimSection       = _keepInteractiveSubtrees ? document.getElementById('sec-simulator') : null;
  const _oldSimDetailSection = _keepInteractiveSubtrees ? document.getElementById('sec-simulator-detail') : null;

  // The dense Option Chain block is never part of the `h` string above (it
  // only contains a `#chain-anchor` placeholder for it) — it's always the
  // same persistent DOM node, moved into place after every rebuild rather
  // than rebuilt, so its scroll position, click-to-reveal state, and live
  // data binding survive full rebuilds unconditionally (not just when the
  // strategy list is unchanged).
  const _chainSection    = document.getElementById('sec-chain');

  const _prevScrollY = window.scrollY;
  // Full rebuild replaces the chain table too, which would otherwise reset
  // its internal scroll on every live tick — capture it first so it can be
  // restored below unless we're deliberately re-centering on ATM.
  const _prevChainEl = $i('chain-scroll');
  const _prevChainScrollTop = _prevChainEl ? _prevChainEl.scrollTop : null;
  $i('dashboard').innerHTML = h;
  if(dashEl) dashEl.dataset.stratsSig = _stratsSig;
  if (window.moveExpirySelectIntoTopBar) moveExpirySelectIntoTopBar();
  // Top-bar content (VIX pill, badges, etc.) can change its rendered
  // height on any tick, so re-measure the sticky stack after each rebuild.
  requestAnimationFrame(updateStickyOffsets);
  // Full rebuild replaces every node, which resets scroll position; put it
  // back so a live tick doesn't yank the page while someone's reading it.
  window.scrollTo(0, _prevScrollY);
  requestAnimationFrame(app.chain.sizeAndScrollChain.bind(app.chain, _prevChainScrollTop));

  // Swap the whole old subtrees back in first (covers their canvases too),
  // then fall back to the narrower canvas-only swap below for whichever
  // ones weren't preserved (e.g. the very first render, or a tick where
  // the strategy list actually changed).
  if(_oldStratsSection){
    const fresh = document.getElementById('sec-strats');
    if(fresh && fresh.parentNode) fresh.parentNode.replaceChild(_oldStratsSection, fresh);
  }
  if(_oldGreeksMoneySect){
    const fresh = document.getElementById('sec-greeks-moneyness');
    if(fresh && fresh.parentNode) fresh.parentNode.replaceChild(_oldGreeksMoneySect, fresh);
  }
  if(_oldSimSection){
    const fresh = document.getElementById('sec-simulator');
    if(fresh && fresh.parentNode) fresh.parentNode.replaceChild(_oldSimSection, fresh);
  }
  if(_oldSimDetailSection){
    const fresh = document.getElementById('sec-simulator-detail');
    if(fresh && fresh.parentNode) fresh.parentNode.replaceChild(_oldSimDetailSection, fresh);
  }

  // Drop the dense Option Chain block into the anchor point between
  // Decision/Executive boxes and OI Flow. Runs on every full rebuild
  // (not gated by _keepInteractiveSubtrees) since the chain block isn't
  // regenerated by this template at all — only relocated.
  // _chainRightPanel already lives INSIDE _chainSection — it's the second
  // grid column of .chain-layout (see #sec-chain / #rightPanel markup in
  // DashboardPro.html). It used to also be independently re-inserted as a
  // sibling of _chainSection right after moving _chainSection itself,
  // which (a) pulled it out of the 1fr/220px grid it belongs in, making it
  // render as a detached-looking floating box instead of sitting next to
  // the table, and (b) on the following render could hand insertBefore a
  // node whose new position was already inside its own subtree, throwing
  // "the new child element contains the parent" and aborting the entire
  // render (visible as the loader/error screen appearing over stale data).
  // Moving _chainSection alone already carries rightPanel along with it,
  // so the separate move is unnecessary as well as unsafe — removed.
  const _chainAnchor = document.getElementById('chain-anchor');
  if(_chainAnchor && _chainSection && !_chainSection.contains(_chainAnchor)){
    _chainAnchor.parentNode.insertBefore(_chainSection, _chainAnchor);
    _chainAnchor.remove();
  } else if(_chainAnchor){
    _chainAnchor.remove();
  }

  // Swap the freshly-created (blank) canvases out for the old ones so
  // there's no visible flash; renderStratPayoff()/simUpdate() redraw onto
  // them normally a moment later. (No-ops when the whole-subtree swap
  // above already restored them.)
  if(_oldPayoffCanvas){
    const freshPayoffCanvas = document.getElementById('strat-payoff-canvas');
    if(freshPayoffCanvas && freshPayoffCanvas.parentNode) freshPayoffCanvas.parentNode.replaceChild(_oldPayoffCanvas, freshPayoffCanvas);
  }
  if(_oldGexCanvas){
    const freshGexCanvas = document.getElementById('sim-gex-canvas');
    if(freshGexCanvas && freshGexCanvas.parentNode) freshGexCanvas.parentNode.replaceChild(_oldGexCanvas, freshGexCanvas);
  }
  
  // ── POST-RENDER ──
  renderVelocity(_velWin);
  renderGreeksGex(_grkView);
  setTimeout(function(){simInit();},50);
  _afterRenderStratPayoff();
  
  
  if(_greeksVisible){
    document.querySelectorAll('[id^="grk-row-"]').forEach(el=>{el.style.display='';});
    const icon=$i('grk-toggle-icon');
    const btn=$i('grk-toggle-btn');
    if(icon)icon.textContent='▼';
    if(btn)btn.classList.add('on');
  }
  
  updateStickyNav(d);
  
  // Update range nav expiry info
  const expDisplay = document.getElementById('expiry-display');
  const dteDisplay = document.getElementById('dte-display');
  const timeDisplay = document.getElementById('time-display');
  if(expDisplay) expDisplay.textContent = d.expiry || '--';
  if(dteDisplay) dteDisplay.textContent = (d.dte||0) + 'd';
  if(timeDisplay) timeDisplay.textContent = d.refreshTime || '--';
}

  updateStickyNav(d){
  const nav=$i('sec-nav-bar');if(!nav)return;
  const strats=d&&d.strategies&&d.strategies.length;
  const risk=d&&d.risk&&(d.risk.keyLevels||d.risk.ivRegime||d.risk.tradeGrade);
  const hasDec=d&&d.decision&&(d.decision.bias||d.decision.confidence);
  document.querySelectorAll('.sec-btn-strats').forEach(b=>b.style.display=strats?'':'none');
  document.querySelectorAll('.sec-btn-risk').forEach(b=>b.style.display=risk?'':'none');
  document.querySelectorAll('.sec-btn-decision').forEach(b=>b.style.display=hasDec?'':'none');
}

  toggleGreeks(){
  _greeksVisible=!_greeksVisible;
  document.querySelectorAll('[id^="grk-row-"]').forEach(el=>{el.style.display=_greeksVisible?'':'none';});
  const icon=$i('grk-toggle-icon');
  const btn=$i('grk-toggle-btn');
  if(icon)icon.textContent=_greeksVisible?'▼':'▶';
  if(btn)btn.classList.toggle('on',_greeksVisible);
}

  toggleGreekRow(strike){
  const el=$i('grk-row-'+strike);
  if(!el)return;
  el.style.display=el.style.display==='none'?'':'none';
}

  switchChainRange(range, el) {
  _chainRange = range;
  _centerChainOnATM = true;

  ['range-tabs-chain','range-tabs-side'].forEach(gid => {
    const g = document.getElementById(gid);
    if(!g) return;
    g.querySelectorAll('.tab-btn').forEach(b => {
      b.classList.toggle('active-range', b.textContent.trim() === (range === 9999 ? 'All' : '±' + range));
    });
  });

  // ── KEEP THE DENSE CHAIN TABLE (#tbody/#rightPanel) IN SYNC ──
  // It used to have its own ±3/±7/±13/ALL filter bar; that's gone, so it
  // now just follows whatever range the global sidebar selects.
  if (typeof currentRange !== 'undefined') {
    currentRange = (range === 9999) ? 'all' : String(range);
    if (window._lastRows) {
      const _visRows = filterRowsByRange(window._lastRows);
      buildRowsHtml(_visRows);
      renderRightPanel(_visRows);
      requestAnimationFrame(() => app.chain.sizeAndScrollChain(null));
    }
  }

  if(_data) _rerenderChainPanels();
}

  switchVelTab(win, el) {
  _velWin = win;
  ['vel-tabs-chain','vel-tabs-side'].forEach(gid => {
    const g = document.getElementById(gid);
    if(!g) return;
    g.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active-vel'));
    g.querySelectorAll('.tab-btn').forEach(t => {
      if(t.textContent.trim() === win + 'm') t.classList.add('active-vel');
    });
  });

  // ── KEEP THE DENSE CHAIN TABLE (#tbody/#rightPanel) IN SYNC ──
  // It used to have its own 5m/15m/30m toggle; that's gone, so it now
  // just follows whatever window the global sidebar selects.
  if (typeof velocityWindowMin !== 'undefined' && window._lastPayload) {
    velocityWindowMin = win;
    refreshView(window._lastPayload);
  }

  if(_data) _rerenderChainPanels();
}

  // Compact "Option Chain Snapshot" card — sits between the Executive
  // boxes and OI Flow (see renderDashboard below). This was previously
  // only a comment/placeholder (#chain-anchor expected a static #sec-chain
  // block to be moved into it, but that block was removed from
  // DashboardPro.html) — nothing was ever actually built here. The full
  // strike-by-strike ledger (Greeks toggle, buy/sell click cells, Bid/Ask
  // depth) still lives at option-chain.html; this card is just the ATM
  // read plus a link there.
  buildChainSummaryHtml(d){
  const chain = getFilteredChain(d);

  if(!chain.length){
    return `
  <div class="section-card" id="chain-summary-card">
    <div class="section-header"><span class="section-title">📊 Option Chain Snapshot</span></div>
    <div class="dd-empty">Awaiting chain data…</div>
  </div>`;
  }

  // Unit-aware K/L/Cr formatter on the RAW number (not pre-scaled) — same
  // approach as option-chain.js's fmt(), which this card is modeled on.
  // chain-views.js's own global fmt()/fmtK() stop at "L" and never scale
  // to "Cr", so a separate helper is needed here to match that reference
  // layout's units exactly.
  const fmtCrLK = (v) => {
    if(v==null||isNaN(v)) return '—';
    const a = Math.abs(v);
    const s = v<0 ? '-' : '';
    if(a>=1e7) return s+(a/1e7).toFixed(2)+'Cr';
    if(a>=1e5) return s+(a/1e5).toFixed(2)+'L';
    if(a>=1e3) return s+(a/1e3).toFixed(1)+'K';
    return s+a.toFixed(0);
  };
  const signedFmt = (v) => (v>0?'+':'') + fmtCrLK(v);
  const netClr = (v) => v>0?'var(--green)':v<0?'var(--red)':'var(--txt3)';

  // ── OI summary ──
  const totalCe = chain.reduce((s,r)=>s+(r.ceOI||0),0);
  const totalPe = chain.reduce((s,r)=>s+(r.peOI||0),0);
  const oiTotal = totalCe+totalPe || 1;
  const pcr = totalPe/(totalCe||1);

  // ── Chg OI summary (+ how much that shifted PCR) ──
  const totalCeChg = chain.reduce((s,r)=>s+(r.ceChgOI||0),0);
  const totalPeChg = chain.reduce((s,r)=>s+(r.peChgOI||0),0);
  const chgTotal = Math.abs(totalCeChg)+Math.abs(totalPeChg) || 1;
  const prevCe = totalCe-totalCeChg, prevPe = totalPe-totalPeChg;
  const prevPcr = prevPe/(prevCe||1);
  const pcrShift = pcr-prevPcr;

  const netOi = totalPe-totalCe;
  const netChgOi = totalPeChg-totalCeChg;

  // ── dOI across 5/15/30m — net PE vs CE change per window, summed over
  // the currently visible strikes ──
  const VEL_WINDOWS = [5,15,30];
  const doiCols = VEL_WINDOWS.map(w=>{
    const block = (d.oiVelocity||[]).find(b=>b.window===w);
    const byStrike = {};
    if(block&&block.rows) block.rows.forEach(vr=>{byStrike[vr.strike]=vr;});
    const ceSum = chain.reduce((s,r)=>s+((byStrike[r.strike]||{}).ceDOI||0),0);
    const peSum = chain.reduce((s,r)=>s+((byStrike[r.strike]||{}).peDOI||0),0);
    return {w, ceSum, peSum, net: peSum-ceSum};
  });

  // ── Volume / OI ratio ──
  const totalCeVol = chain.reduce((s,r)=>s+(r.ceVol||0),0);
  const totalPeVol = chain.reduce((s,r)=>s+(r.peVol||0),0);
  const ratioCap = 3;
  const ceRatio = totalCeVol/(totalCe||1);
  const peRatio = totalPeVol/(totalPe||1);

  return `
  <div class="section-card" id="chain-summary-card">
    <div class="section-header">
      <span class="section-title">📊 Option Chain Snapshot</span>
      <button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="window.open('option-chain.html','_blank')">Full Chain →</button>
    </div>
    <div style="display:grid;grid-template-columns:1.15fr 1.15fr 1fr 1fr;gap:16px;padding:10px 2px 4px;">

      <div>
        <div style="font-size:10px;color:var(--txt3);margin-bottom:8px;letter-spacing:.04em;">OI SUMMARY</div>
        <div style="height:6px;border-radius:999px;overflow:hidden;display:flex;background:var(--bg2);margin-bottom:8px;">
          <div style="width:${(totalPe/oiTotal)*100}%;background:linear-gradient(90deg,var(--green),transparent);"></div>
          <div style="width:${(totalCe/oiTotal)*100}%;background:linear-gradient(90deg,transparent,var(--red));"></div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;font-family:var(--mono);font-size:13px;font-weight:700;flex-wrap:wrap;">
          <span style="color:var(--green);">${fmtCrLK(totalPe)}</span>
          <span style="font-size:9px;color:var(--txt3);font-weight:400;">PE</span>
          <span style="background:rgba(245,166,35,.15);color:var(--amber);padding:2px 8px;border-radius:999px;font-size:11px;">PCR ${fmtN(pcr,2)}</span>
          <span style="font-size:9px;color:var(--txt3);font-weight:400;">CE</span>
          <span style="color:var(--red);">${fmtCrLK(totalCe)}</span>
        </div>
        <div style="font-size:11px;color:${netClr(netOi)};margin-top:6px;font-family:var(--mono);">Net (PE−CE) <b>${signedFmt(netOi)}</b></div>
      </div>

      <div>
        <div style="font-size:10px;color:var(--txt3);margin-bottom:8px;letter-spacing:.04em;">CHG OI SUMMARY</div>
        <div style="height:6px;border-radius:999px;overflow:hidden;display:flex;background:var(--bg2);margin-bottom:8px;">
          <div style="width:${(Math.abs(totalPeChg)/chgTotal)*100}%;background:linear-gradient(90deg,var(--green),transparent);"></div>
          <div style="width:${(Math.abs(totalCeChg)/chgTotal)*100}%;background:linear-gradient(90deg,transparent,var(--red));"></div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;font-family:var(--mono);font-size:13px;font-weight:700;flex-wrap:wrap;">
          <span style="color:var(--green);">${signedFmt(totalPeChg)}</span>
          <span style="font-size:9px;color:var(--txt3);font-weight:400;">PE</span>
          <span style="background:rgba(245,166,35,.15);color:var(--amber);padding:2px 8px;border-radius:999px;font-size:11px;">PCR Δ ${signedFmt(pcrShift)}</span>
          <span style="font-size:9px;color:var(--txt3);font-weight:400;">CE</span>
          <span style="color:var(--red);">${signedFmt(totalCeChg)}</span>
        </div>
        <div style="font-size:11px;color:${netClr(netChgOi)};margin-top:6px;font-family:var(--mono);">Net (PE−CE) <b>${signedFmt(netChgOi)}</b></div>
      </div>

      <div>
        <div style="font-size:10px;color:var(--txt3);margin-bottom:8px;letter-spacing:.04em;">dOI <span style="font-weight:400;">5 · 15 · 30m</span></div>
        <div style="display:flex;justify-content:space-between;gap:6px;">
          ${doiCols.map(c=>`
          <div style="text-align:center;flex:1;">
            <div style="font-size:10px;font-family:var(--mono);color:var(--green);">${signedFmt(c.peSum)}</div>
            <div style="font-size:10px;font-family:var(--mono);color:var(--red);margin-bottom:2px;">${signedFmt(c.ceSum)}</div>
            <div style="font-size:9px;color:var(--txt3);">${c.w}m</div>
            <div style="font-size:9px;font-weight:700;padding:1px 6px;border-radius:999px;margin-top:2px;display:inline-block;color:${netClr(c.net)};background:var(--bg2);">net ${signedFmt(c.net)}</div>
          </div>`).join('')}
        </div>
      </div>

      <div>
        <div style="font-size:10px;color:var(--txt3);margin-bottom:8px;letter-spacing:.04em;">VOLUME <span style="font-weight:400;">&amp; Vol/OI</span></div>
        <div style="display:flex;align-items:center;gap:6px;font-size:11px;margin-bottom:6px;">
          <span style="color:var(--red);min-width:18px;">CE</span>
          <span style="color:var(--red);font-family:var(--mono);min-width:56px;">${fmtCrLK(totalCeVol)}</span>
          <div style="flex:1;height:5px;border-radius:999px;background:var(--bg2);overflow:hidden;"><div style="height:100%;width:${Math.min(100,(ceRatio/ratioCap)*100)}%;background:var(--red);"></div></div>
          <span style="font-family:var(--mono);color:var(--txt3);font-size:10px;">${fmtN(ceRatio,2)}x</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;font-size:11px;">
          <span style="color:var(--green);min-width:18px;">PE</span>
          <span style="color:var(--green);font-family:var(--mono);min-width:56px;">${fmtCrLK(totalPeVol)}</span>
          <div style="flex:1;height:5px;border-radius:999px;background:var(--bg2);overflow:hidden;"><div style="height:100%;width:${Math.min(100,(peRatio/ratioCap)*100)}%;background:var(--green);"></div></div>
          <span style="font-family:var(--mono);color:var(--txt3);font-size:10px;">${fmtN(peRatio,2)}x</span>
        </div>
      </div>

    </div>
  </div>`;
}

  sizeAndScrollChain(prevScrollTop){
  const wrap=$i('chain-scroll');
  if(!wrap)return;
  const thead=wrap.querySelector('thead');
  const sampleRow=wrap.querySelector('tbody tr');
  if(sampleRow){
    const rowH=sampleRow.getBoundingClientRect().height||32;
    const theadH=thead?thead.getBoundingClientRect().height:0;
    // Viewport always shows 7 strike-rows regardless of which ATM range
    // (±5/±7/±10/All etc.) is currently selected in the range filter — the
    // range only controls how many total strikes get loaded into the
    // scrollable list; this fixed height is what makes the rest scrollable
    // by sliding up/down within the box instead of growing the page.
    wrap.style.maxHeight=Math.round(theadH+rowH*7)+'px';
  }
  if(_centerChainOnATM){
    const atmRow=$i('chain-row-atm');
    if(atmRow){
      const target=atmRow.offsetTop-(wrap.clientHeight/2)+(atmRow.clientHeight/2);
      wrap.scrollTop=Math.max(target,0);
    }
    _centerChainOnATM=false;
  }else if(prevScrollTop!=null){
    wrap.scrollTop=prevScrollTop;
  }
}

  renderVelocity(win){
  const el=$i('vel-content');if(!el||!_data)return;
  const vel=_data.oiVelocity;
  if(!vel||!vel.length){el.innerHTML='<div style="font-size:12px;color:var(--txt3);padding:8px 0;">No OI velocity data.</div>';return;}
  const block=vel.find(b=>b.window===win)||vel[0];
  const chainStrikes=new Set(getFilteredChain(_data).map(c=>c.strike));
  const rows=(block.rows||[]).filter(r=>chainStrikes.size===0||chainStrikes.has(r.strike));
  if(!rows.length){el.innerHTML=`<div style="font-size:12px;color:var(--txt3);padding:8px 0;">No data for ${win}-min window.</div>`;return;}
  const maxAbs=Math.max(...rows.map(r=>Math.max(Math.abs(r.ceDOI||0),Math.abs(r.peDOI||0))),1);
  const atm=activeAtm(_data);
  let h=`<table class="t"><thead><tr>
    <th style="text-align:center;width:62px;">Strike</th>
    <th style="width:56px;">CE now</th><th style="width:90px;">CE ΔOI</th><th style="width:44px;">CE LTP</th>
    <th style="width:56px;">PE now</th><th style="width:90px;">PE ΔOI</th><th style="width:44px;">PE LTP</th>
    <th style="text-align:left;width:96px;">Signal</th>
  </tr></thead><tbody>`;
  rows.forEach(r=>{
    const ia=r.strike===atm;const sc=ia?' atm-sc':'sc';
    function velDOICell(v,maxAbs){
      const pct=maxAbs>0?Math.min(Math.abs(v)/maxAbs*24,24):0;
      const bar=v>=0?`<div style="width:${pct.toFixed(0)}px;background:var(--green);border-radius:2px;height:8px;display:inline-block;flex-shrink:1;max-width:24px;"></div>`:`<div style="width:${pct.toFixed(0)}px;background:var(--red);border-radius:2px;height:8px;display:inline-block;flex-shrink:1;max-width:24px;"></div>`;
      return `<div style="display:flex;align-items:center;gap:4px;justify-content:flex-end;overflow:hidden;min-width:0;">${bar}<span style="color:${sClr(v)};font-size:10px;font-family:var(--mono);white-space:nowrap;flex-shrink:0;">${v>=0?'+':''}${fmtK(v)}</span></div>`;
    }
    h+=`<tr>
      <td class="${sc}">${fmtI(r.strike)}${ia?' ★':''}</td>
      <td style="font-size:10px;color:var(--txt2);">${fmtK(r.ceNow)}</td>
      <td>${velDOICell(r.ceDOI,maxAbs)}</td>
      <td style="font-weight:600;font-family:var(--mono);">${fmtN(r.ceLTP,1)}</td>
      <td style="font-size:10px;color:var(--txt2);">${fmtK(r.peNow)}</td>
      <td>${velDOICell(r.peDOI,maxAbs)}</td>
      <td style="font-weight:600;font-family:var(--mono);">${fmtN(r.peLTP,1)}</td>
      <td style="text-align:left;"><span class="sp sp-n">${r.signal||'—'}</span></td>
    </tr>`;
  });
  const netCE=rows.reduce((s,r)=>s+(r.ceDOI||0),0);
  const netPE=rows.reduce((s,r)=>s+(r.peDOI||0),0);
  h+=`</tbody></table>
    <div class="section-footer">
      <span>CE builds: <strong style="color:var(--red);">${rows.filter(r=>r.ceDOI>0).length}/${rows.length}</strong></span>
      <span>PE builds: <strong style="color:var(--green);">${rows.filter(r=>r.peDOI>0).length}/${rows.length}</strong></span>
      <span>Net CE ΔOI: <strong style="color:${sClr(netCE)}">${netCE>=0?'+':''}${fmtK(netCE)}</strong></span>
      <span>Net PE ΔOI: <strong style="color:${sClr(netPE)}">${netPE>=0?'+':''}${fmtK(netPE)}</strong></span>
      <span>Window: <strong>${win} min</strong></span>
    </div>`;
  el.innerHTML=h;
}

  switchGrkTab(view,el){
  _grkView=view;
  document.querySelectorAll('#grk-tabs .tab-btn').forEach(t=>t.classList.remove('active-grk'));
  el.classList.add('active-grk');
  renderGreeksGex(view);
}

  // ── GREEKS ALERTS (main-dashboard summary card) ──
  // The full per-strike Greeks/GEX table (Δ/Γ/Θ/Vega tabs + Net GEX +
  // Regime columns) moved out of the main dashboard into its own modal —
  // openGreeksModal()/closeGreeksModal() in ModalManager, mirroring the
  // existing OI Dashboard modal — so it never crowds the main view. What
  // stays inline here is just the handful of things worth reacting to:
  // a gamma-flip strike sitting inside the visible ATM range, a
  // short-gamma dealer regime (hedging flows amplify rather than dampen
  // moves), and unusually fast theta burn relative to the ATM straddle's
  // own premium. The %/day threshold below is a tunable heuristic — the
  // backend doesn't send an explicit "this is high" flag — not a value
  // pulled from the payload.
  buildGreeksAlertsHtml(greeks, atm, d){
  const GREEKS_ALERT_THETA_PCT = 5; // ATM theta/day as % of ATM straddle premium
  const straddle = (d.callPremium||0) + (d.putPremium||0);
  const totalGEX = greeks.reduce((s,g)=>s+(g.netGEX||0),0);
  const flipRow  = findGammaFlipStrike(greeks);
  const thetaPct = straddle>0 ? Math.abs(d.atmTheta||0)/straddle*100 : 0;

  const alerts=[];
  if(flipRow){
    alerts.push({
      icon:'⚡', clr:'var(--amber)',
      text:`Gamma flip at <strong>${fmtI(flipRow.strike)}</strong> — regime crosses ${flipRow.netGEX>=0?'short → long':'long → short'} γ there`
    });
  }
  if(totalGEX<0){
    alerts.push({
      icon:'⚠', clr:'var(--red)',
      text:`Dealer <strong>short gamma</strong> (${fmtN(totalGEX,3)}B) — hedging flows likely amplify moves`
    });
  }
  if(thetaPct>GREEKS_ALERT_THETA_PCT){
    alerts.push({
      icon:'⏳', clr:'var(--red)',
      text:`High theta decay — ATM straddle losing <strong>${fmtN(thetaPct,1)}%</strong> of premium/day`
    });
  }

  const rows = alerts.length
    ? alerts.map(a=>`<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:rgba(255,255,255,0.03);border-left:2px solid ${a.clr};border-radius:4px;font-size:12px;line-height:1.4;"><span style="flex-shrink:0;">${a.icon}</span><span style="color:var(--txt2);">${a.text}</span></div>`).join('')
    : `<div style="font-size:12px;color:var(--txt3);padding:6px 8px;">No Greek alerts right now — γ regime stable, theta normal.</div>`;

  return `<div class="section-card algn-card" id="greeks-alerts-card" style="min-width:0;">
    <div class="section-header">
      <span class="section-title">Greeks / Net GEX</span>
      <button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="openGreeksModal()" title="Open full Greeks &amp; GEX table">Full Table →</button>
    </div>
    <div style="display:flex;flex-direction:column;gap:6px;padding:2px 0;">
      ${rows}
    </div>
  </div>`;
}

  // Grouped next to the alerts card above (both are "Greeks" info). Pulled
  // out into its own method — same as buildGreeksAlertsHtml — so the
  // incremental expiry-switch refresh in _rerenderChainPanels can rebuild
  // this exact card instead of duplicating the markup; previously this had
  // no id and only ever updated on a full rebuild, so it went stale
  // between expiry switches.
  buildAtmGreeksHtml(d){
  return `<div class="section-card" id="atm-greeks-card">
      <div class="section-header"><span class="section-title">ATM Greeks ${fmtI(d.atm)}</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">Delta</span><span style="font-weight:600;font-family:var(--mono);">${fmtN(d.atmDelta,4)}</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">Gamma ×10⁴</span><span style="font-weight:600;font-family:var(--mono);">${fmtN(d.atmGamma,4)}</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">Theta / day</span><span style="color:var(--red);font-weight:600;font-family:var(--mono);">${fmtN(d.atmTheta,2)}</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">Vega</span><span style="font-weight:600;font-family:var(--mono);">${fmtN(d.atmVega,2)}</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">IV vs HV</span><span style="color:var(--amber);font-weight:600;">${fmtN((d.atmIV||0)-(d.hv30||0),2)}% rich</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">IV rank</span><span style="color:var(--blue);font-weight:600;">${Math.round(d.ivRank||0)} / 100</span></div>
    </div>`;
}

  // ── IV ALERTS (main-dashboard summary card) ──
  // Same treatment as buildGreeksAlertsHtml: the full per-strike IV
  // surface (CE/PE bars, ATM ± 3) moved into its own modal
  // (openIvSurfaceModal()), so the main view only surfaces two things
  // worth reacting to — elevated put/call skew, and an IV rank sitting
  // near either extreme (options unusually rich or unusually cheap).
  // Thresholds are tunable heuristics, not backend-supplied flags.
  buildIvAlertsHtml(d, chain, atm){
  const IV_ALERT_SKEW_PCT = 1.5;   // |atmSkew| above this is called "elevated"
  const IV_ALERT_RANK_HIGH = 80;   // ivRank above this is called "rich"
  const IV_ALERT_RANK_LOW  = 20;   // ivRank below this is called "cheap"
  const skew = d.atmSkew||0;
  const rank = d.ivRank||0;

  const alerts=[];
  if(Math.abs(skew) > IV_ALERT_SKEW_PCT){
    alerts.push({
      icon:'📐', clr:'var(--amber)',
      text:`Elevated ${skew>0?'put':'call'} skew — <strong>${fmtN(skew,2)}%</strong> at ATM`
    });
  }
  if(rank >= IV_ALERT_RANK_HIGH){
    alerts.push({
      icon:'🔺', clr:'var(--red)',
      text:`IV rank <strong>${Math.round(rank)}/100</strong> — options historically rich, consider selling premium`
    });
  } else if(rank <= IV_ALERT_RANK_LOW){
    alerts.push({
      icon:'🔻', clr:'var(--green)',
      text:`IV rank <strong>${Math.round(rank)}/100</strong> — options historically cheap, consider buying premium`
    });
  }

  const rows = alerts.length
    ? alerts.map(a=>`<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:rgba(255,255,255,0.03);border-left:2px solid ${a.clr};border-radius:4px;font-size:12px;line-height:1.4;"><span style="flex-shrink:0;">${a.icon}</span><span style="color:var(--txt2);">${a.text}</span></div>`).join('')
    : `<div style="font-size:12px;color:var(--txt3);padding:6px 8px;">No IV alerts right now — skew and rank both in normal range.</div>`;

  return `<div class="section-card" id="iv-alerts-card">
    <div class="section-header">
      <span class="section-title">IV Surface</span>
      <button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="openIvSurfaceModal()" title="Open full IV surface">Full Surface →</button>
    </div>
    <div style="display:flex;flex-direction:column;gap:6px;padding:2px 0;">
      ${rows}
    </div>
  </div>`;
}

  // ── FULL IV SURFACE (modal content) ──
  // Same per-strike CE/PE bar table + Skew/Max IV/Min IV footer that used
  // to render inline in the main template. Pulled out into its own method
  // so it can be (a) written once into the modal's static content div and
  // (b) refreshed from that same place on every tick / expiry switch via
  // renderIvSurfaceModal() below, instead of duplicating this markup in
  // both the initial template and the incremental-refresh path.
  buildIvSurfaceHtml(d, chain, atm){
  const atmIdx = chain.findIndex(r => r.atm || r.strike === atm);
  let ivRows = [];
  if (atmIdx >= 0) {
    const start = Math.max(0, atmIdx - 3);
    const end = Math.min(chain.length, atmIdx + 4);
    ivRows = chain.slice(start, end);
  } else {
    ivRows = chain.slice(0, 6);
  }
  const maxIV = Math.max(...ivRows.map(r => Math.max(r.ceIV||0, r.peIV||0)), 1);
  const barMaxWidth = 160;

  let rowsHtml = '';
  ivRows.forEach(r => {
    const ia = r.atm || r.strike === atm;
    const ceIV = r.ceIV || 0;
    const peIV = r.peIV || 0;
    const ceWidth = Math.max((ceIV / maxIV) * barMaxWidth, 4);
    const peWidth = Math.max((peIV / maxIV) * barMaxWidth, 4);
    rowsHtml += `<div style="display:grid;grid-template-columns:1fr 80px 1fr;align-items:center;gap:0;padding:3px 6px;${ia?'background:rgba(18,184,134,0.08);border-radius:4px;':''}">
      <div style="display:flex;align-items:center;justify-content:flex-end;gap:5px;">
        <span style="font-size:9px;font-family:var(--mono);color:var(--red);font-weight:600;white-space:nowrap;">${fmtN(ceIV,2)}%</span>
        <div style="height:8px;border-radius:3px 0 0 3px;background:var(--red);width:${ceWidth}px;min-width:3px;flex-shrink:0;"></div>
      </div>
      <div style="text-align:center;padding:0 4px;">
        <span style="font-family:var(--mono);font-size:10px;font-weight:${ia?700:400};color:${ia?'var(--green)':'var(--txt3)'};">${fmtI(r.strike)}${ia?' ★':''}</span>
      </div>
      <div style="display:flex;align-items:center;justify-content:flex-start;gap:5px;">
        <div style="height:8px;border-radius:0 3px 3px 0;background:var(--green);width:${peWidth}px;min-width:3px;flex-shrink:0;"></div>
        <span style="font-size:9px;font-family:var(--mono);color:var(--green);font-weight:600;white-space:nowrap;">${fmtN(peIV,2)}%</span>
      </div>
    </div>`;
  });
  const minIV = Math.min(...ivRows.map(r => Math.min(r.ceIV||0, r.peIV||0)));

  return `<div style="display:flex;flex-direction:column;gap:4px;">${rowsHtml}</div>
    <div style="font-size:11px;color:var(--txt3);margin-top:10px;padding-top:10px;border-top:1px solid var(--border);display:flex;gap:20px;flex-wrap:wrap;">
      <span>Skew <strong style="color:var(--amber);">${fmtN(d.atmSkew,2)}%</strong> at ATM</span>
      <span>Max IV <strong style="color:var(--red);">${fmtN(maxIV,2)}%</strong></span>
      <span>Min IV <strong style="color:var(--green);">${fmtN(minIV,2)}%</strong></span>
    </div>`;
}

  // Writes the full IV surface (buildIvSurfaceHtml above) into the modal's
  // static content div. Reads _data itself (same pattern as
  // renderGreeksGex(view) below) so it can be called with no args from
  // renderDashboard's post-render block, live ticks, and expiry switches.
  renderIvSurfaceModal(){
  const el = $i('iv-surface-content');
  if(!el || !_data) return;
  const chain = getFilteredChain(_data);
  const atm = activeAtm(_data);
  el.innerHTML = this.buildIvSurfaceHtml(_data, chain, atm);
}

  // Merged Greeks + Net GEX table. One <td> per strike shared by both
  // datasets (previously two separate cards each repeating the strike
  // column). The Δ/Γ/Θ/Vega tabs only swap which Greek fills the CE/PE
  // columns — the Net GEX / Regime columns are always shown alongside.
  renderGreeksGex(view){
  const el=$i('grkgex-content');if(!el||!_data)return;
  const grkStrikeSet=new Set(getFilteredChain(_data).map(c=>c.strike));
  const greeks=(_data.greeks||[]).filter(g=>grkStrikeSet.has(g.strike));
  if(!greeks.length){el.innerHTML='<div style="font-size:12px;color:var(--txt3);padding:8px 0;">No Greeks/GEX data.</div>';return;}
  const atm=activeAtm(_data);
  const fieldMap={
    delta:{ceKey:'cDelta',peKey:'pDelta',label:'Delta',ceClr:'var(--red)',peClr:'var(--green)',fmt:v=>fmtN(v,4)},
    gamma:{ceKey:'cGamma',peKey:'pGamma',label:'Gamma×10⁴',ceClr:'var(--amber)',peClr:'var(--amber)',fmt:v=>fmtN(v,4)},
    theta:{ceKey:'cTheta',peKey:'pTheta',label:'Theta/day',ceClr:'var(--red)',peClr:'var(--red)',fmt:v=>fmtN(v,2)},
    vega:{ceKey:'cVega',peKey:'pVega',label:'Vega/1%',ceClr:'var(--green)',peClr:'var(--green)',fmt:v=>fmtN(v,2)},
  };
  const f=fieldMap[view]||fieldMap.delta;
  const grkVals=greeks.map(g=>Math.max(Math.abs(g[f.ceKey]||0),Math.abs(g[f.peKey]||0)));
  const maxGrk=Math.max(...grkVals,0.0001);
  const gexVals=greeks.map(g=>Math.abs(g.netGEX||0));
  const maxGex=Math.max(...gexVals,0.0001);
  // Bars shrunk vs the old 2-panel layout (was up to 40px) since each row
  // now carries 4 data columns (CE/PE Greek + GEX + Regime) instead of 2.
  const BAR_MAX=28;
  function miniBar(v,max,color,fmt){
    const pct=Math.min(Math.abs(v)/max*BAR_MAX,BAR_MAX);
    const clr=color||(v>=0?'var(--green)':'var(--red)');
    return `<div style="display:flex;align-items:center;gap:5px;min-width:0;"><div style="width:${pct.toFixed(0)}px;height:8px;background:${clr};border-radius:3px;flex-shrink:0;"></div><span style="font-size:10px;font-weight:600;color:${clr};font-family:var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;">${fmt(v)}</span></div>`;
  }
  const flipStrike=findGammaFlipStrike(greeks);
  const flipStrikeVal=flipStrike?flipStrike.strike:null;
  let h=`<table class="t"><thead><tr>
    <th style="text-align:center;width:64px;">Strike</th>
    <th style="width:36px;">IV%</th>
    <th style="text-align:left;width:22%;">CE ${f.label}</th>
    <th style="text-align:left;width:22%;">PE ${f.label}</th>
    <th style="text-align:left;padding-left:10px;width:28%;">Net GEX</th>
    <th style="width:50px;text-align:center;transform:translateX(-8px);">Regime</th>
  </tr></thead><tbody>`;
  greeks.forEach(g=>{
    const ia=g.strike===atm;const sc=ia?' atm-sc':'sc';
    const ceV=g[f.ceKey]||0;const peV=g[f.peKey]||0;const gexV=g.netGEX||0;
    // Flip strike (regime transition row) gets a dashed top border as a
    // visual anchor — it now sits inline with delta/greek data too.
    const isFlip=flipStrikeVal!=null&&g.strike===flipStrikeVal;
    const rowStyle=isFlip?' style="border-top:1px dashed var(--txt3);"':'';
    h+=`<tr${rowStyle}>
      <td class="${sc}" style="white-space:nowrap;">${fmtI(g.strike)}${ia?' ★':''}</td>
      <td style="color:var(--amber);">${fmtN(g.iv,2)}%</td>
      <td>${miniBar(ceV,maxGrk,f.ceClr,f.fmt)}</td>
      <td>${miniBar(peV,maxGrk,f.peClr,f.fmt)}</td>
      <td style="padding-left:10px;">${miniBar(gexV,maxGex,gexV>=0?'var(--blue)':'var(--red)',v=>fmtN(v,3)+'B')}</td>
      <td style="text-align:center;font-size:9px;color:${gexV>=0?'var(--blue)':'var(--red)'};font-weight:600;transform:translateX(-8px);">${gexV>=0?'Long':'Short'}</td>
    </tr>`;
  });
  h+=`</tbody></table>`;
  el.innerHTML=h;
  const totalGEX=greeks.reduce((s,g)=>s+(g.netGEX||0),0);
  const footEl=$i('grkgex-footer');
  if(footEl){
    footEl.innerHTML=`
      <span>Total: <strong style="color:${sClr(totalGEX)};">${fmtN(totalGEX,3)}B</strong></span>
      <span style="color:${totalGEX>=0?'var(--blue)':'var(--red)'};">${totalGEX>=0?'Dealer long γ — dampens':'Dealer short γ — amplifies'}</span>
      ${flipStrike?`<span>Flip: <strong>${fmtI(flipStrike.strike)}</strong></span>`:''}
    `;
  }
}

  onExpiryChange(selectedExpiry){
  if(!_data) return;
  const activeExpiry = _data._primaryExpiry || _data.expiry || '';
  const chainStore   = _data.chains    || {};
  const metaStore    = _data.chainMeta || {};

  if(selectedExpiry === activeExpiry){
    _selectedExpiry = null;
    // Restore all primary data
    _data.expiry     = activeExpiry;
    _data._activeExpiry = activeExpiry;
    _data.chain      = _data._primaryChain   || _data.chain;
    _data.greeks     = _data._primaryGreeks  || _data.greeks;
    _data.atm        = _data._primaryAtm     || _data.atm;
    _data.dte        = _data._primaryDte     || _data.dte;
    _data.ceWall     = _data._primaryCeWall  || _data.ceWall;
    _data.peWall     = _data._primaryPeWall  || _data.peWall;
    _data.maxPain    = _data._primaryMaxPain || _data.maxPain;
    _data.totalPCR   = _data._primaryPCR     || _data.totalPCR;
    _data.callPremium= _data._primaryCallPremium || _data.callPremium;
    _data.putPremium = _data._primaryPutPremium  || _data.putPremium;
    _data.atmIV      = _data._primaryAtmIV   || _data.atmIV;
    _data.atmDelta   = _data._primaryAtmDelta ?? _data.atmDelta;
    _data.atmGamma   = _data._primaryAtmGamma ?? _data.atmGamma;
    _data.atmTheta   = _data._primaryAtmTheta ?? _data.atmTheta;
    _data.atmVega    = _data._primaryAtmVega  ?? _data.atmVega;
  } else if(chainStore[selectedExpiry] || (_expiryViewCache[selectedExpiry] && _expiryViewCache[selectedExpiry].chain)){
    _selectedExpiry = selectedExpiry;
    _data._activeExpiry = selectedExpiry;
    // Stash primary values on first switch so we can restore later
    if(!_data._primaryChain){
      _data._primaryChain       = _data.chain;
      _data._primaryGreeks      = _data.greeks;
      _data._primaryAtm         = _data.atm;
      _data._primaryDte         = _data.dte;
      _data._primaryCeWall      = _data.ceWall;
      _data._primaryPeWall      = _data.peWall;
      _data._primaryMaxPain     = _data.maxPain;
      _data._primaryPCR         = _data.totalPCR;
      _data._primaryCallPremium = _data.callPremium;
      _data._primaryPutPremium  = _data.putPremium;
      _data._primaryAtmIV       = _data.atmIV;
      _data._primaryAtmDelta    = _data.atmDelta;
      _data._primaryAtmGamma    = _data.atmGamma;
      _data._primaryAtmTheta    = _data.atmTheta;
      _data._primaryAtmVega     = _data.atmVega;
    }
    _data.expiry = selectedExpiry;
    const cached = _expiryViewCache[selectedExpiry] || {};
    _data.chain = chainStore[selectedExpiry] || cached.chain;
    const meta  = metaStore[selectedExpiry] || cached.meta || {};
    _expiryViewCache[selectedExpiry] = { chain: _data.chain, meta };
    if(meta.greeks      != null) _data.greeks      = meta.greeks;
    if(meta.atm         != null) _data.atm          = meta.atm;
    if(meta.dte         != null) _data.dte          = meta.dte;
    if(meta.ceWall      != null) _data.ceWall       = meta.ceWall;
    if(meta.peWall      != null) _data.peWall       = meta.peWall;
    if(meta.maxPain     != null) _data.maxPain      = meta.maxPain;
    if(meta.totalPCR    != null) _data.totalPCR     = meta.totalPCR;
    if(meta.straddle    != null){ _data.callPremium = meta.straddle/2; _data.putPremium = meta.straddle/2; }
    if(meta.callPremium != null) _data.callPremium  = meta.callPremium;
    if(meta.putPremium  != null) _data.putPremium   = meta.putPremium;
    if(meta.atmIV       != null) _data.atmIV        = meta.atmIV;
    if(meta.atmDelta    != null) _data.atmDelta     = meta.atmDelta;
    if(meta.atmGamma    != null) _data.atmGamma     = meta.atmGamma;
    if(meta.atmTheta    != null) _data.atmTheta     = meta.atmTheta;
    if(meta.atmVega     != null) _data.atmVega      = meta.atmVega;
    if(!_data.atm) _data.atm = activeAtm(_data);
  }
  // Re-render every chain-derived panel
  _rerenderChainPanels();
  // Single expiry control drives everything, including the native Option
  // Chain table — refresh it immediately instead of waiting for the next
  // WebSocket tick.
  app.chainDense.refreshView(_data);
}

  _rerenderChainPanels(){
  if(!_data) return;

  const chain          = getFilteredChain(_data);
  const chainStrikeSet = new Set(chain.map(r=>r.strike));
  const atm            = activeAtm(_data);
  const greeksAll      = _data.greeks || [];
  const greeks         = greeksAll.filter(g=>chainStrikeSet.has(g.strike));
  const velBlock       = (_data.oiVelocity||[]).find(b=>b.window===_velWin)||(_data.oiVelocity||[])[0];
  const velByStrike    = {};
  if(velBlock&&velBlock.rows) velBlock.rows.forEach(vr=>{velByStrike[vr.strike]=vr;});
  const velMax         = Math.max(...chain.map(r=>{const vr=velByStrike[r.strike]||{};return Math.max(Math.abs(vr.ceDOI||0),Math.abs(vr.peDOI||0));}),1);
  const oiAnnot        = (_data.decision&&_data.decision.oiAnnotations)||{};
  const maxOI          = Math.max(...chain.map(r=>Math.max(r.ceOI||0,r.peOI||0)),1);

  // ── 1. Chain table body ───────────────────────────────────────────────────
  const chainEl = document.getElementById('chain-body');
  if(chainEl){
    let rows='';
    chain.forEach(r=>{
      const ia=r.atm||r.strike===atm; const ac=ia?' atm':''; const acs=ia?' atm-sc':'sc';
      const g=greeks.find(x=>x.strike===r.strike)||{};
      const sk=r.strike;
      const vr=velByStrike[sk]||{};
      const ceVelDOI=vr.ceDOI!=null?vr.ceDOI:0;
      const peVelDOI=vr.peDOI!=null?vr.peDOI:0;
      const cs=combinedSignal(r.ceSignal,r.peSignal);
      const annot=oiAnnot[String(sk)]||{};
      const rowTitle=annot.ce||annot.pe?`CE: ${annot.ce||'—'} | PE: ${annot.pe||'—'}`:'Click to show/hide Greeks';
      rows+=`<tr${ia?' id="chain-row-atm"':''} style="cursor:pointer;" onclick="toggleGreekRow(${sk})" title="${rowTitle}">`;
      rows+=`<td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.ceVol)}</td>
        <td class="${ac}">${velMiniCell(ceVelDOI,velMax,ceOiChgClr(ceVelDOI))}</td>
        <td class="${ac} pt-ltp-click" style="font-weight:600;font-family:var(--mono);" onclick="ptOpenQuickOrder(event,${sk},'CE',${r.ceLTP!=null?r.ceLTP:'null'})" title="Click to trade this strike">${fmtN(r.ceLTP,1)}</td>
        <td class="${ac}" style="color:${ceOiChgClr(r.ceDOI)};font-size:10px;">${(r.ceDOI||0)>=0?'+':''}${fmtK(r.ceDOI)}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.ceOI)}</td>
        <td class="${acs}" style="white-space:nowrap;line-height:1.15;">${fmtI(r.strike)}${ia?' ★':''}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.peOI)}</td>
        <td class="${ac}" style="color:${sClr(r.peDOI)};font-size:10px;">${(r.peDOI||0)>=0?'+':''}${fmtK(r.peDOI)}</td>
        <td class="${ac} pt-ltp-click" style="font-weight:600;font-family:var(--mono);" onclick="ptOpenQuickOrder(event,${sk},'PE',${r.peLTP!=null?r.peLTP:'null'})" title="Click to trade this strike">${fmtN(r.peLTP,1)}</td>
        <td class="${ac}">${velMiniCell(peVelDOI,velMax,sClr(peVelDOI))}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.peVol)}</td>
        <td style="text-align:right;padding-right:10px;"><span class="sp ${cs.cls}">${cs.label}</span></td>
      </tr>
      ${g.cDelta!=null?`<tr id="grk-row-${sk}" class="grk-row" style="display:none;">
        <td colspan="12" style="text-align:left;padding:4px 12px;white-space:nowrap;font-size:10px;color:var(--txt3);">
          <span style="display:inline-block;min-width:140px;">CΔ <strong style="color:var(--blue);">${fmtN(g.cDelta,3)}</strong> &nbsp;Γ×10⁴ <strong style="color:var(--amber);">${fmtN(g.cGamma,3)}</strong> &nbsp;Θ <strong style="color:var(--red);">${fmtN(g.cTheta,2)}</strong> &nbsp;Vega <strong style="color:var(--blue);">${fmtN(g.cVega,2)}</strong></span>
          <span style="display:inline-block;min-width:80px;margin-left:8px;">GEX <strong style="color:${sClr(g.netGEX||0)};">${fmtN(g.netGEX,3)}B</strong></span>
          <span style="display:inline-block;min-width:110px;margin-left:30px;">CE IV <strong style="color:var(--red);">${fmtN(r.ceIV,2)}%</strong> &nbsp;PE IV <strong style="color:var(--green);">${fmtN(r.peIV,2)}%</strong></span>
          <span style="display:inline-block;min-width:80px;margin-left:20px;">PΔ <strong style="color:var(--red);">${fmtN(g.pDelta,3)}</strong></span>
          <span style="display:inline-block;min-width:160px;margin-left:20px;">CE Signal <strong class="sp ${spClass(r.ceSignal)}">${r.ceSignal||'—'}</strong></span>
          <span style="display:inline-block;min-width:160px;margin-left:10px;">PE Signal <strong class="sp ${spClass(r.peSignal)}">${r.peSignal||'—'}</strong></span>
        </td>
      </tr>`:''}`;
    });
    chainEl.innerHTML = rows;
    if(_greeksVisible) document.querySelectorAll('[id^="grk-row-"]').forEach(el=>{el.style.display='';});
    _centerChainOnATM=true; // expiry just changed — snap the viewport back to ATM ±5
    requestAnimationFrame(()=>app.chain.sizeAndScrollChain(null));
  }

  // ── 2. DTE pill ──────────────────────────────────────────────────────────
  const dteEl = document.getElementById('dte-display');
  if(dteEl){
    const dte = _data.dte || 0;
    dteEl.textContent = dte+'d';
    dteEl.style.color = dte<=1?'var(--red)':dte<=3?'var(--amber)':'var(--amber)';
  }

  // ── 3. Right analytics panel ──────────────────────────────────────────────
  const rpEl = document.querySelector('.chain-right-panel');
  if(rpEl){
    const totCeOI  = chain.reduce((s,r)=>s+(r.ceOI||0),0);
    const totPeOI  = chain.reduce((s,r)=>s+(r.peOI||0),0);
    const totCeDOI = chain.reduce((s,r)=>s+(r.ceChgOI||0),0);
    const totPeDOI = chain.reduce((s,r)=>s+(r.peChgOI||0),0);
    const velBlockRP=((_data.oiVelocity||[]).find(b=>b.window===_velWin)||(_data.oiVelocity||[])[0]);
    const totCeVel=(velBlockRP&&velBlockRP.rows||[]).filter(r=>chainStrikeSet.has(r.strike)).reduce((s,r)=>s+(r.ceDOI||0),0);
    const totPeVel=(velBlockRP&&velBlockRP.rows||[]).filter(r=>chainStrikeSet.has(r.strike)).reduce((s,r)=>s+(r.peDOI||0),0);
    const maxDOIrp=Math.max(Math.abs(totCeDOI),Math.abs(totPeDOI),1);
    const maxVelrp=Math.max(Math.abs(totCeVel),Math.abs(totPeVel),1);
    function rpBar(v,max,clr){const w=Math.max(Math.round(Math.abs(v)/max*72),2);return `<div class="crp-spark-wrap"><div class="crp-spark" style="width:${w}px;background:${clr};"></div><span style="font-size:9px;font-family:var(--mono);color:${clr};">${fmtK(v)}</span></div>`;}
    const bullStrikes=chain.filter(r=>{const cs=combinedSignal(r.ceSignal,r.peSignal);return cs.cls==='sp-strongbull'||cs.cls==='sp-bull';}).length;
    const bearStrikes=chain.filter(r=>{const cs=combinedSignal(r.ceSignal,r.peSignal);return cs.cls==='sp-strongbear'||cs.cls==='sp-bear';}).length;
    const aggBias=bullStrikes>bearStrikes?{label:'Bullish',cls:'sp-bull'}:bearStrikes>bullStrikes?{label:'Bearish',cls:'sp-bear'}:{label:'Mixed',cls:'sp-mixed'};
    const panelPCR=totCeOI>0?(totPeOI/totCeOI).toFixed(2):'—';
    const pcrColor=parseFloat(panelPCR)>1?'var(--green)':parseFloat(panelPCR)<0.8?'var(--red)':'var(--amber)';
    const netOI=totPeOI-totCeOI; const netDOI=totPeDOI-totCeDOI; const netVel=totPeVel-totCeVel;
    const netAbsMax=Math.max(Math.abs(netOI),Math.abs(netDOI),Math.abs(netVel),1);
    const arpBarW=(v,max)=>Math.max(Math.round(Math.abs(v)/max*72),3);
    const arpClr=(v)=>v>=0?'var(--green)':'var(--red)';
    const totCeVolChg=chain.reduce((s,r)=>s+(r.ceVolChg||0),0);
    const totPeVolChg=chain.reduce((s,r)=>s+(r.peVolChg||0),0);
    const maxVolChg=Math.max(Math.abs(totCeVolChg),Math.abs(totPeVolChg),1);
    // ── VOL VEL FIX (v2) ──
    // v1 diffed against the *previous render*, which fires almost every WS
    // tick (multiple times a second) — not a "(${_velWin}m)" velocity at
    // all, just a sub-second delta. That's why it was spiking from near-zero
    // to large and back on every tick instead of showing a stable 5-minute
    // trend. Fix: keep a timestamped history buffer and diff each strike's
    // ceVol/peVol against the snapshot closest to _velWin minutes ago —
    // same windowing concept the backend's oiVelocity block already applies
    // to OI Vel, just computed client-side since no equivalent volume field
    // exists in that payload.
    this._volHistory = this._volHistory || [];
    const _now = Date.now();
    const _nowSnap = {};
    chain.forEach(r=>{ _nowSnap[r.strike] = { ceVol: r.ceVol, peVol: r.peVol }; });
    this._volHistory.push({ ts: _now, snap: _nowSnap });
    const _windowMs = _velWin * 60 * 1000;
    const _cutoff = _now - _windowMs;
    // Trim history once entries are more than one window past the cutoff —
    // keeps memory bounded without discarding the reference sample we need.
    while (this._volHistory.length > 1 && this._volHistory[1].ts < _cutoff - _windowMs) this._volHistory.shift();
    // Pick the newest sample that's still at least a full window old. Until
    // enough history has accumulated (e.g. just after page load), this
    // falls back to the oldest sample available — the window is shorter
    // than _velWin for the first few minutes, then self-corrects.
    let _refSnap = this._volHistory[0].snap;
    for (const h of this._volHistory) { if (h.ts <= _cutoff) _refSnap = h.snap; else break; }
    let totCeVelVol = 0, totPeVelVol = 0;
    chain.forEach(r=>{
      const prev = _refSnap[r.strike];
      if (prev) {
        if (r.ceVol != null && prev.ceVol != null) totCeVelVol += (r.ceVol - prev.ceVol);
        if (r.peVol != null && prev.peVol != null) totPeVelVol += (r.peVol - prev.peVol);
      }
    });
    const maxVelVol=Math.max(Math.abs(totCeVelVol),Math.abs(totPeVelVol),1);
    rpEl.innerHTML=`
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-bottom:8px;">
      <div class="arp-row" style="padding-bottom:5px;margin-bottom:4px;border-bottom:1px solid var(--border);">
        <span class="arp-key">Signal</span>
        <div class="arp-val"><span class="sp ${aggBias.cls}" style="font-size:10px;font-weight:700;">${aggBias.label}</span><span style="font-size:9px;color:var(--txt3);margin-left:4px;">${bullStrikes}↑ ${bearStrikes}↓</span></div>
      </div>
      <div class="arp-row"><span class="arp-key">Net OI</span><div class="arp-val"><span class="arp-num" style="color:${arpClr(netOI)};">${netOI>=0?'+':''}${fmtK(netOI)}</span><div class="arp-bar" style="width:${arpBarW(netOI,netAbsMax)}px;background:${arpClr(netOI)};"></div></div></div>
      <div class="arp-row"><span class="arp-key">Chg OI</span><div class="arp-val"><span class="arp-num" style="color:${arpClr(netDOI)};">${netDOI>=0?'+':''}${fmtK(netDOI)}</span><div class="arp-bar" style="width:${arpBarW(netDOI,netAbsMax)}px;background:${arpClr(netDOI)};"></div></div></div>
      <div class="arp-row"><span class="arp-key">Vel OI</span><div class="arp-val"><span class="arp-num" style="color:${arpClr(netVel)};">${netVel>=0?'+':''}${fmtK(netVel)}</span><div class="arp-bar" style="width:${arpBarW(netVel,netAbsMax)}px;background:${arpClr(netVel)};"></div></div></div>
      <div style="padding-top:5px;margin-top:4px;border-top:1px solid var(--border);">
        <div style="margin-bottom:6px;">
          <div style="display:flex;justify-content:space-between;font-size:8px;font-family:var(--mono);margin-bottom:2px;">
            <span style="color:var(--red);">CE ${totCeOI>0?Math.round(totCeOI/(totCeOI+totPeOI)*100):50}%</span>
            <span style="color:var(--txt3);font-size:7px;text-transform:uppercase;letter-spacing:.05em;">OI Split</span>
            <span style="color:var(--green);">PE ${totPeOI>0?Math.round(totPeOI/(totCeOI+totPeOI)*100):50}%</span>
          </div>
          <div class="oi-flow-bar">
            <div class="oi-flow-ce" style="flex:${totCeOI>0?Math.round(totCeOI/(totCeOI+totPeOI)*100):50};"></div>
            <div class="oi-flow-pe" style="flex:${totPeOI>0?Math.round(totPeOI/(totCeOI+totPeOI)*100):50};"></div>
          </div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;">
        <span class="arp-key">PCR <span style="font-size:8px;font-weight:400;text-transform:none;">(visible)</span></span>
        <span style="font-size:14px;font-weight:700;font-family:var(--mono);color:${pcrColor};">${panelPCR}</span>
        </div>
      </div>
    </div>
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-bottom:8px;">
      <div class="crp-title" style="margin-bottom:6px;">OI Analytics <span style="color:var(--txt3);font-weight:400;">(${_velWin}m)</span></div>
      <div style="display:grid;grid-template-columns:64px 1fr 1fr;gap:2px;margin-bottom:4px;">
        <div></div><div class="crp-head-ce">CE</div><div class="crp-head-pe">PE</div>
      </div>
      <div class="crp-row"><span class="crp-label">OI</span><div class="crp-ce">${fmtK(totCeOI)}</div><div class="crp-pe">${fmtK(totPeOI)}</div></div>
      <div class="crp-row"><span class="crp-label">Chg OI</span>${rpBar(totCeDOI,maxDOIrp,totCeDOI>=0?'var(--red)':'var(--green)')}${rpBar(totPeDOI,maxDOIrp,totPeDOI>=0?'var(--green)':'var(--red)')}</div>
      <div class="crp-row"><span class="crp-label">OI Vel</span>${rpBar(totCeVel,maxVelrp,totCeVel>=0?'var(--red)':'var(--green)')}${rpBar(totPeVel,maxVelrp,totPeVel>=0?'var(--green)':'var(--red)')}</div>
    </div>
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;">
      <div class="crp-title" style="margin-bottom:6px;">Volume Analytics <span style="color:var(--txt3);font-weight:400;">(${_velWin}m)</span></div>
      <div style="display:grid;grid-template-columns:64px 1fr 1fr;gap:2px;margin-bottom:4px;">
        <div></div><div class="crp-head-ce">CE</div><div class="crp-head-pe">PE</div>
      </div>
      <div class="crp-row"><span class="crp-label">Vol</span><div class="crp-ce">${fmtK(chain.reduce((s,r)=>s+(r.ceVol||0),0))}</div><div class="crp-pe">${fmtK(chain.reduce((s,r)=>s+(r.peVol||0),0))}</div></div>
      <div class="crp-row"><span class="crp-label">Vol Chg</span>${rpBar(totCeVolChg,maxVolChg,totCeVolChg>=0?'var(--red)':'var(--green)')}${rpBar(totPeVolChg,maxVolChg,totPeVolChg>=0?'var(--green)':'var(--red)')}</div>
      <div class="crp-row"><span class="crp-label">Vol Vel</span>${rpBar(totCeVelVol,maxVelVol,totCeVelVol>=0?'var(--red)':'var(--green)')}${rpBar(totPeVelVol,maxVelVol,totPeVelVol>=0?'var(--green)':'var(--red)')}</div>
    </div>`;
  }

  // 4. OI Flow Snapshot card (compact — full butterfly table now lives in
  // the OI Dashboard's Butterfly tab, see buildOiFlowSummaryHtml()).
  const oiFlowSummaryEl = document.getElementById("oi-flow-summary-card");
  if(oiFlowSummaryEl){
    const freshOiFlowSummary = buildOiFlowSummaryHtml(chain, atm, velByStrike);
    // buildOiFlowSummaryHtml() returns the whole card including its own
    // #oi-flow-summary-card wrapper — swap the wrapper's contents/attrs via
    // outerHTML so setHtmlIfChanged's dataset-diff cache stays meaningful
    // (it lives on the element itself, which outerHTML replaces wholesale).
    if(oiFlowSummaryEl.dataset.lastHtml !== freshOiFlowSummary){
      oiFlowSummaryEl.outerHTML = freshOiFlowSummary;
      const fresh = document.getElementById("oi-flow-summary-card");
      if(fresh) fresh.dataset.lastHtml = freshOiFlowSummary;
    }
  }

  // 4b. Greeks summary — alerts card (gamma flip / short-gamma regime /
  // theta decay) and the ATM Greeks card next to it, same outerHTML-diff
  // treatment as the OI Flow card above, so an expiry switch reflects the
  // new expiry's Greeks immediately instead of waiting for the next tick
  // (or, for ATM Greeks, the next full rebuild — it had no id before and
  // never got an incremental refresh at all).
  const greeksAlertsEl = document.getElementById("greeks-alerts-card");
  if(greeksAlertsEl){
    const freshGreeksAlerts = app.chain.buildGreeksAlertsHtml(greeks, atm, _data);
    if(greeksAlertsEl.dataset.lastHtml !== freshGreeksAlerts){
      greeksAlertsEl.outerHTML = freshGreeksAlerts;
      const fresh = document.getElementById("greeks-alerts-card");
      if(fresh) fresh.dataset.lastHtml = freshGreeksAlerts;
    }
  }
  const atmGreeksEl = document.getElementById("atm-greeks-card");
  if(atmGreeksEl){
    const freshAtmGreeks = app.chain.buildAtmGreeksHtml(_data);
    if(atmGreeksEl.dataset.lastHtml !== freshAtmGreeks){
      atmGreeksEl.outerHTML = freshAtmGreeks;
      const fresh = document.getElementById("atm-greeks-card");
      if(fresh) fresh.dataset.lastHtml = freshAtmGreeks;
    }
  }

  // ── 6. IV Surface ─────────────────────────────────────────────────────────
  const ivSurfEl = document.querySelector('#sec-iv .section-card');
  if(ivSurfEl){
    const atmIdx=chain.findIndex(r=>r.atm||r.strike===atm);
    let ivRows=[];
    if(atmIdx>=0){const st=Math.max(0,atmIdx-3),en=Math.min(chain.length,atmIdx+4);ivRows=chain.slice(st,en);}
    else{ivRows=chain.slice(0,6);}
    const maxIV=Math.max(...ivRows.map(r=>Math.max(r.ceIV||0,r.peIV||0)),1);
    const barMaxWidth=160;
    let ivHtml='<div style="display:flex;flex-direction:column;gap:4px;">';
    ivRows.forEach(r=>{
      const ia=r.atm||r.strike===atm;
      const ceIV=r.ceIV||0,peIV=r.peIV||0;
      const ceW=Math.max((ceIV/maxIV)*barMaxWidth,4);
      const peW=Math.max((peIV/maxIV)*barMaxWidth,4);
      ivHtml+=`<div style="display:grid;grid-template-columns:1fr 80px 1fr;align-items:center;gap:0;padding:3px 6px;${ia?'background:rgba(18,184,134,0.08);border-radius:4px;':''}">
        <div style="display:flex;align-items:center;justify-content:flex-end;gap:5px;">
          <span style="font-size:9px;font-family:var(--mono);color:var(--red);font-weight:600;white-space:nowrap;">${fmtN(ceIV,2)}%</span>
          <div style="height:8px;border-radius:3px 0 0 3px;background:var(--red);width:${ceW}px;min-width:3px;flex-shrink:0;"></div>
        </div>
        <div style="text-align:center;padding:0 4px;">
          <span style="font-family:var(--mono);font-size:10px;font-weight:${ia?700:400};color:${ia?'var(--green)':'var(--txt3)'};">${fmtI(r.strike)}${ia?' ★':''}</span>
        </div>
        <div style="display:flex;align-items:center;justify-content:flex-start;gap:5px;">
          <div style="height:8px;border-radius:0 3px 3px 0;background:var(--green);width:${peW}px;min-width:3px;flex-shrink:0;"></div>
          <span style="font-size:9px;font-family:var(--mono);color:var(--green);font-weight:600;white-space:nowrap;">${fmtN(peIV,2)}%</span>
        </div>
      </div>`;
    });
    const minIV=Math.min(...ivRows.map(r=>Math.min(r.ceIV||0,r.peIV||0)));
    ivHtml+=`</div><div style="font-size:11px;color:var(--txt3);margin-top:10px;padding-top:10px;border-top:1px solid var(--border);display:flex;gap:20px;flex-wrap:wrap;">
      <span>Skew <strong style="color:var(--amber);">${fmtN(_data.atmSkew,2)}%</strong> at ATM</span>
      <span>Max IV <strong style="color:var(--red);">${fmtN(maxIV,2)}%</strong></span>
      <span>Min IV <strong style="color:var(--green);">${fmtN(minIV,2)}%</strong></span>
    </div>`;
    const hdr3=ivSurfEl.querySelector('.section-header');
    ivSurfEl.innerHTML='';
    if(hdr3) ivSurfEl.appendChild(hdr3);
    const ivDiv=document.createElement('div');
    ivDiv.innerHTML=ivHtml;
    while(ivDiv.firstChild) ivSurfEl.appendChild(ivDiv.firstChild);
  }

  // ── 7. Greeks & GEX panels ───────────────────────────────────────────────
  renderGreeksGex(_grkView);

  // ── 8. OI Velocity panel ─────────────────────────────────────────────────
  renderVelocity(_velWin);

  // ── 9. Institutional F&O Simulator + Scenario Controls ─────────────────────
  // This was missing entirely: expiry switches only ever refreshed the 8
  // panels above, so the simulator's GEX chart/stats/table/vol-grid kept
  // showing whatever expiry was loaded first, and moving the Scenario
  // Control sliders had no visible effect until the next full page reload.
  if (document.getElementById('sim-gex-canvas')) simInit();

  // ── 10. Executive dashboard (Market Health / Market Story / Top Movers) ────
  // Same gap as above — this block was only ever built once, during the
  // full renderDashboard() pass, so GEX/PCR/theta figures in these three
  // cards went stale after an expiry-only switch.
  const execWrap = document.getElementById('exec-section-wrap');
  if (execWrap) {
    _data.totalGEX = greeks.reduce((s,g)=>s+(g.netGEX||0),0);
    execWrap.outerHTML = renderExecutiveDashboard(_data);
  }

  if (window.updateGreeksMoneynessChart) window.updateGreeksMoneynessChart(_data);
}
}