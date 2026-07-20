// ============================================================
// chain-depth.js
// Phase 2 chain-view decomposition — see chain-view.js's header comment
// for the full split rationale and load-order requirement (this file
// must load after chain-view.js, and before dashboard.js).
//
// This file holds everything to do with per-strike row/depth data: the
// dense chain's payload -> row mapping and its expanded per-strike detail
// panel (ChainDenseView), plus the right-hand analytics panel and its own
// Bid/Ask depth box (RightPanelView). Moved verbatim from chain-views.js.
// ============================================================

ChainDenseView.prototype.mapPayloadToRows = function(payload) {
    const chainArr = payload.chain || [];
    const expiryKey = payload.expiry || "";
    const velLookup = buildVelocityLookup(payload);
    // Full 5/15/30m trend, independent of which single window this
    // dashboard's own toggle (velocityWindowMin) currently has selected.
    // The standalone option-chain.html tab's OI-velocity sparkline
    // (velBars()) and its 5m/15m/30m summary row both need all three
    // windows at once — previously nothing built this, so `velTrend` was
    // undefined on every broadcast row and the tab rendered 0 for every
    // strike no matter how much OI had actually moved.
    const velTrendByStrike = {};
    [5, 15, 30].forEach((w, i) => {
      const winBlock = (payload.oiVelocity || []).find((b) => b.window === w);
      (winBlock && winBlock.rows || []).forEach((r) => {
        const entry = velTrendByStrike[r.strike] || (velTrendByStrike[r.strike] = { ce: [0, 0, 0], pe: [0, 0, 0] });
        entry.ce[i] = r.ceDOI || 0;
        entry.pe[i] = r.peDOI || 0;
      });
    });
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

    // payload.greeks is a separate array (one entry per strike: cDelta/
    // cGamma/cTheta/cVega, pDelta/pGamma/pTheta/pVega — see
    // chain-view-models.js's buildStrikeDetailViewModel, which joins the
    // same array by strike for the main dashboard's detail panel). The
    // main dashboard's own rendering path reads that array directly and
    // never needed it merged onto ce/pe here — but chain-sync.js's
    // broadcast to the standalone option-chain.html tab only ever sends
    // `rows` (this function's output), not payload.greeks. Without the
    // join below, every ce/pe leg on the standalone tab has no
    // delta/gamma/theta/vega field at all, so its Greeks row renders
    // "—" for every value on every strike, live data or not.
    const greeksByStrike = {};
    (payload.greeks || []).forEach((g) => { greeksByStrike[g.strike] = g; });

    const rows = chainArr.map((row) => {
      const rowKey = expiryKey + "_" + row.strike;
      const vel = velLookup[row.strike] || {};
      const trend = velTrendByStrike[row.strike] || { ce: [0, 0, 0], pe: [0, 0, 0] };
      const prev = this.prevSnapshot[rowKey] || {};
      const g = greeksByStrike[row.strike] || {};

      const ceIvChg = prev.ceIV != null && row.ceIV != null ? +(row.ceIV - prev.ceIV).toFixed(2) : null;
      const peIvChg = prev.peIV != null && row.peIV != null ? +(row.peIV - prev.peIV).toFixed(2) : null;
      const ceVolVel = prev.ceVol != null && row.ceVol != null ? row.ceVol - prev.ceVol : null;
      const peVolVel = prev.peVol != null && row.peVol != null ? row.peVol - prev.peVol : null;
      const ceLtpChg = row.ceChg != null ? row.ceChg : null;
      const peLtpChg = row.peChg != null ? row.peChg : null;

      const ce = {
        iv: row.ceIV, ivChg: ceIvChg, vol: row.ceVol, volChg: row.ceVolChg,
        volPct: row.ceVol ? (((row.ceOI || 0) / row.ceVol) * 100).toFixed(1) : null,
        ltp: row.ceLTP, chg: ceLtpChg, chgPct: row.cePChg, oi: row.ceOI, oiChg: row.ceChgOI,
        oiVel: vel.ceVel, velTrend: trend.ce, volVel: ceVolVel, signal: row.ceSignal,
        bid: row.ceBid, bidQty: row.ceBidQty, ask: row.ceAsk, askQty: row.ceAskQty,
        totalBidQty: row.ceTotalBidQty, totalAskQty: row.ceTotalAskQty,
        delta: g.cDelta, gamma: g.cGamma, theta: g.cTheta, vega: g.cVega,
      };
      const pe = {
        iv: row.peIV, ivChg: peIvChg, vol: row.peVol, volChg: row.peVolChg,
        volPct: row.peVol ? (((row.peOI || 0) / row.peVol) * 100).toFixed(1) : null,
        ltp: row.peLTP, chg: peLtpChg, chgPct: row.pePChg, oi: row.peOI, oiChg: row.peChgOI,
        oiVel: vel.peVel, velTrend: trend.pe, volVel: peVolVel, signal: row.peSignal,
        bid: row.peBid, bidQty: row.peBidQty, ask: row.peAsk, askQty: row.peAskQty,
        totalBidQty: row.peTotalBidQty, totalAskQty: row.peTotalAskQty,
        delta: g.pDelta, gamma: g.pGamma, theta: g.pTheta, vega: g.pVega,
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
};

// NOTE on what this panel shows: LTP/IV/OI/OI-Velocity/Volume are already
// visible in the collapsed row directly above this panel (and
// PCR/Combined Signal in the strike cell + rightmost column), so
// repeating them here just made every expanded strike look identical at
// a glance. This panel only shows what ISN'T already on screen: Bid/Ask
// depth, the per-leg Greeks, Net GEX, and the per-leg (CE/PE) Signal.
//
// Phase 3 split: the calculations that used to live in this function body
// (hasGreeks, per-leg color/delta/gamma/theta/vega selection, bid/ask
// strings, Net GEX sign-based color) now live in
// buildStrikeDetailViewModel() (chain-view-models.js) as plain-object
// derivation; the markup itself now lives in renderStrikeDetailTemplate()
// (chain-templates.js). This method is kept as a thin wrapper — same
// signature, same return value — for any existing caller.
ChainDenseView.prototype.buildStrikeDetailHtml = function(r, g) {
    return renderStrikeDetailTemplate(buildStrikeDetailViewModel(r, g));
};

ChainDenseView.prototype.filterRowsByRange = function(rows) {
    if (currentRange === "all") return rows;
    const n = parseInt(currentRange, 10);
    const atmIndex = rows.findIndex((r) => r.isAtm);
    if (atmIndex === -1) return rows;
    return rows.slice(Math.max(0, atmIndex - n), atmIndex + n + 1);
};

ChainDenseView.prototype.buildVelocityLookup = function(payload) {
    const lookup = {};
    const win = (payload.oiVelocity || []).find((w) => w.window === this.velocityWindowMin);
    if (win && win.rows) {
      win.rows.forEach((r) => { lookup[r.strike] = { ceVel: r.ceDOI, peVel: r.peDOI }; });
    }
    return lookup;
};

ChainDenseView.prototype.selectDepthStrike = function(strike) {
    _selectedDepthStrike = _selectedDepthStrike === strike ? null : strike;
    if (app.chainDense.lastRows) {
      const _visRows = filterRowsByRange(app.chainDense.lastRows);
      buildRowsHtml(_visRows);
      renderRightPanel(_visRows);
      if (_greeksVisible) document.querySelectorAll('[id^="grk-row-"]').forEach((el) => { el.style.display = ""; });
    }
};

RightPanelView.prototype.renderRightPanel = function(rows) {
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
};

RightPanelView.prototype.buildDepthBoxHtml = function(rows) {
    const atmRow = rows.find((r) => r.isAtm) || rows[Math.floor(rows.length / 2)];
    const pinnedRow = _selectedDepthStrike != null ? rows.find((r) => r.strike === _selectedDepthStrike) : null;
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
};

RightPanelView.prototype.rpBar = function(v, max, clr) {
    const w = Math.max(Math.round((Math.abs(v) / max) * 128), 2);
    return `<div class="crp-spark-wrap"><div class="crp-spark" style="width:${w}px;background-image:${tickFill(clr)};"></div><span style="font-size:9px;font-family:var(--mono);color:${clr};">${fmt(v)}</span></div>`;
};