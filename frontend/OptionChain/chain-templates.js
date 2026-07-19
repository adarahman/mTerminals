// ============================================================
// chain-templates.js
// Phase 3 rendering/logic split (see master optimization prompt, Task
// "Remove HTML generation from business logic"). Companion file to
// chain-view-models.js — see that file's header comment for the full
// split rationale.
//
// Every function here takes a view-model object built by
// chain-view-models.js and returns an HTML string by pure interpolation.
// No formatting/derivation calls (sign/dirClass/fmt/fmtN/
// chainCombinedSignal/spClass, arithmetic, .toFixed, or a ternary that
// derives NEW meaning from raw data) belong in this file — every value
// used below was already computed onto the view model. The one exception
// is cell(...): it's an existing presentational helper (formatters.js)
// that just wraps four already-computed values in markup, the same way
// a <Cell/> component would — it takes no raw row/leg data, so calling it
// here isn't reintroducing business logic.
//
// This is a pure code-motion split: every markup fragment below is
// byte-identical to what used to be inline in the old
// ChainDenseView.prototype.buildRowsHtml() / buildStrikeDetailHtml()
// template strings — so HTML output is unchanged.
//
// Must load after chain-view-models.js is not required at parse time
// (only at call time), but is placed after it in DashboardPro.html for
// readability. Must load before chain-renderer.js and chain-depth.js,
// which call into it. See DashboardPro.html script order.
// ============================================================

// ── The expanded per-strike summary panel ──
function renderStrikeDetailTemplate(vm) {
  const legBlockHtml = (leg) => `
        <div style="min-width:230px;">
          <div style="font-weight:700;color:${leg.color};margin-bottom:4px;">${leg.sideLabel}</div>
          <div>Bid <strong>${leg.bidStr}</strong> &nbsp;/&nbsp; Ask <strong>${leg.askStr}</strong></div>
          ${leg.hasGreeks ? `<div>&Delta; <strong>${leg.deltaText}</strong> &nbsp;&Gamma;&times;10&#8308; <strong>${leg.gammaText}</strong> &nbsp;&Theta; <strong>${leg.thetaText}</strong> &nbsp;Vega <strong>${leg.vegaText}</strong></div>` : ''}
          <div>Signal <strong class="sp ${leg.signalClass}">${leg.signalLabel}</strong></div>
        </div>`;
  return `
      <div style="display:flex;gap:28px;flex-wrap:wrap;align-items:flex-start;padding:8px 12px;font-size:10.5px;color:var(--text-faint);line-height:1.6;">
        ${legBlockHtml(vm.ce)}
        <div style="min-width:140px;">
          <div style="font-weight:700;color:var(--oc-amber);margin-bottom:4px;">STRIKE ${vm.strike}</div>
          ${vm.hasGreeks ? `<div>Net GEX <strong style="color:${vm.netGEXColor};">${vm.netGEXText}</strong></div>` : ''}
        </div>
        ${legBlockHtml(vm.pe)}
      </div>`;
}

// ── One dense-chain table row (collapsed row + its hidden detail row) ──
function renderChainRowTemplate(vm) {
  let html = `<tr class="${vm.rowClass}"${vm.rowIdAttr} style="cursor:pointer;" onclick="toggleGreekRow(${vm.strike})" title="Click for full strike summary">`;
  html += `<td>${cell(vm.ce.ivText, vm.ce.ivDelta, "flat", vm.ce.ivDeltaClass)}</td>`;
  html += `<td>${cell(vm.ce.volText, vm.ce.volSub, "flat", "flat")}</td>`;
  html += `<td class="pt-ltp-click" onclick="event.stopPropagation();ptOpenQuickOrder(event,${vm.strike},'CE',${vm.ce.ltpRaw != null ? vm.ce.ltpRaw : 'null'})" title="Click to trade this strike">${cell(vm.ce.ltpText, vm.ce.ltpDelta, vm.ce.ltpClass, vm.ce.ltpClass)}</td>`;
  html += `<td>${cell(vm.ce.velText, vm.ce.velSub, vm.ce.velClass, "flat")}</td>`;
  html += `<td class="oi-bar"><div class="fill ce" style="width:${vm.ce.oiFillPct}%"></div>${cell(vm.ce.oiText, vm.ce.oiDelta, "flat", vm.ce.oiDeltaClass)}</td>`;
  html += `<td class="strike" title="Click to pin Bid/Ask Depth — summary also shown below" onclick="event.stopPropagation();selectDepthStrike(${vm.strike});toggleGreekRow(${vm.strike})">${cell(vm.strike, vm.pcrText, "", vm.pcrDeltaClass)}</td>`;
  html += `<td class="oi-bar"><div class="fill pe" style="width:${vm.pe.oiFillPct}%"></div>${cell(vm.pe.oiText, vm.pe.oiDelta, "flat", vm.pe.oiDeltaClass)}</td>`;
  html += `<td>${cell(vm.pe.velText, vm.pe.velSub, vm.pe.velClass, "flat")}</td>`;
  html += `<td class="pt-ltp-click" onclick="event.stopPropagation();ptOpenQuickOrder(event,${vm.strike},'PE',${vm.pe.ltpRaw != null ? vm.pe.ltpRaw : 'null'})" title="Click to trade this strike">${cell(vm.pe.ltpText, vm.pe.ltpDelta, vm.pe.ltpClass, vm.pe.ltpClass)}</td>`;
  html += `<td>${cell(vm.pe.volText, vm.pe.volSub, "flat", "flat")}</td>`;
  html += `<td>${cell(vm.pe.ivText, vm.pe.ivDelta, "flat", vm.pe.ivDeltaClass)}</td>`;
  html += `<td class="sig-col"><span class="sig ${vm.signalCls}">${vm.signalLabel}</span></td>`;
  html += `</tr>`;
  // Two <tr> elements instead of native <details>/<summary>, because
  // <summary> is not a valid child of <tr>/<tbody>; browsers silently
  // hoist it out of the table and the click handler never fires where
  // expected. (Same note as the pre-split version.)
  html += `<tr id="grk-row-${vm.strike}" class="grk-row" style="display:none;">
        <td colspan="12" style="text-align:left;padding:0;">${renderStrikeDetailTemplate(vm.detail)}</td>
      </tr>`;
  return html;
}
