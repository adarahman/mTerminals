// ============================================================
// modal-manager.js
// Extracted verbatim from panels-views.js, which had grown into five
// unrelated classes stapled into one 2200+ line file (OiFlowView,
// ExecView, StrategyView, SimulatorView, ModalManager). This is a pure
// code-motion split — ModalManager's body is byte-identical to what was
// previously the last class in panels-views.js — no behavior change.
//
// ModalManager is the single instance (app.modal, constructed in
// dashboard.js) that owns open/close + Escape-key handling for the native
// full-screen dashboard modals. The global shim layer keeps every
// existing onclick="..." attribute in DashboardPro.html and every
// cross-file bare call (data-service.js, dashboard-panels.js) working
// unchanged after this split — see dashboard.js's shim block for the
// full list.
//
// LOAD ORDER: must load after panels-views.js's other four classes are no
// longer a dependency (ModalManager doesn't reference them directly) but
// must load before dashboard.js, since dashboard.js's App constructor does
// `new ModalManager()`. See DashboardPro.html script order.
// ============================================================

class ModalManager {
  constructor() {
    this._activeModal = null;
    this._activeCloseFn = null;
    this._modalInvokers = new WeakMap();
    this._modalKeyHandlers = new WeakMap();
    this._modalBackdropHandlers = new WeakMap();
  }

  // Defensive guard shared by every open*Modal() method below: closes any
  // other .oc-modal that's still marked 'open' before opening this one.
  // Every modal already closes itself via its own close*Modal()/Escape
  // handler, so in normal use this is a no-op — but if two "expand"
  // triggers ever fire back to back without a close in between (e.g. a
  // fast double-click, or a future modal added without wiring its own
  // close call first), this is what stops a second full-screen overlay
  // from silently stacking on top of the first instead of replacing it
  // (which reads as "both panels expanded, one of them blank" — the
  // first modal isn't actually empty, it's just hidden behind the
  // second).
  _openModal(modal, closeFn){
  if(!modal) return;

  let invoker = document.activeElement && document.activeElement !== document.body
    ? document.activeElement : null;

  // Close the previously-active modal through its public close path so
  // modal-specific cleanup (e.g. FII/DII relay disconnect) still runs.
  // If focus currently sits inside that modal, carry forward its original
  // invoker rather than remembering a soon-to-be-hidden close button.
  if(this._activeModal && this._activeModal !== modal && typeof this._activeCloseFn === 'function'){
    if(invoker && this._activeModal.contains(invoker)){
      invoker = this._modalInvokers.get(this._activeModal) || null;
    }
    this._activeCloseFn();
  }

  if(invoker && invoker.isConnected) this._modalInvokers.set(modal, invoker);

  this._activeModal = modal;
  this._activeCloseFn = typeof closeFn === 'function' ? closeFn : null;
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.classList.add('open');
  if(window.eventBus) window.eventBus.emit('modal:open', { id: modal.id || null });

  const oldKeyHandler = this._modalKeyHandlers.get(modal);
  if(oldKeyHandler) modal.removeEventListener('keydown', oldKeyHandler);
  const oldBackdropHandler = this._modalBackdropHandlers.get(modal);
  if(oldBackdropHandler) modal.removeEventListener('click', oldBackdropHandler);

  const panel = modal.querySelector('.oc-modal-panel') || modal;
  if(!panel.hasAttribute('tabindex')) panel.setAttribute('tabindex', '-1');

  const focusableSelector = [
    'button:not([disabled])', 'a[href]', 'input:not([disabled])',
    'select:not([disabled])', 'textarea:not([disabled])', 'iframe',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');

  const keyHandler = (e) => {
    if(e.key === 'Escape'){
      e.preventDefault();
      e.stopPropagation();
      if(typeof closeFn === 'function') closeFn();
      return;
    }
    if(e.key !== 'Tab') return;
    const focusables = Array.from(modal.querySelectorAll(focusableSelector))
      .filter(el => !el.hidden && el.offsetParent !== null);
    if(!focusables.length){
      e.preventDefault();
      panel.focus();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if(e.shiftKey && document.activeElement === first){
      e.preventDefault(); last.focus();
    } else if(!e.shiftKey && document.activeElement === last){
      e.preventDefault(); first.focus();
    }
  };
  modal.addEventListener('keydown', keyHandler);
  this._modalKeyHandlers.set(modal, keyHandler);

  const backdropHandler = (e) => {
    if(e.target !== modal || !modal.classList.contains('open')) return;
    if(typeof closeFn === 'function') closeFn();
  };
  modal.addEventListener('click', backdropHandler);
  this._modalBackdropHandlers.set(modal, backdropHandler);

  requestAnimationFrame(() => {
    const preferred = modal.querySelector('.oc-modal-back') || modal.querySelector(focusableSelector);
    (preferred || panel).focus();
  });
}

  _closeModal(modal){
  if(!modal) return;
  modal.classList.remove('open');
  if(window.eventBus) window.eventBus.emit('modal:close', { id: modal.id || null });

  const keyHandler = this._modalKeyHandlers.get(modal);
  if(keyHandler) modal.removeEventListener('keydown', keyHandler);
  this._modalKeyHandlers.delete(modal);

  const backdropHandler = this._modalBackdropHandlers.get(modal);
  if(backdropHandler) modal.removeEventListener('click', backdropHandler);
  this._modalBackdropHandlers.delete(modal);

  if(this._activeModal === modal){
    this._activeModal = null;
    this._activeCloseFn = null;
  }

  const invoker = this._modalInvokers.get(modal);
  this._modalInvokers.delete(modal);
  if(invoker && invoker.isConnected){
    requestAnimationFrame(() => invoker.focus());
  }
}

  openOIDashboardModal(){
  var modal = document.getElementById('oi-flow-modal');
  if(!modal) return;
  this._openModal(modal, () => this.closeOIDashboardModal());
  app.oiFlow.renderNativeChart(app.oiFlow.nativeChartMode || 'oi');
}

  closeOIDashboardModal(){
  var modal = document.getElementById('oi-flow-modal');
  if(modal) this._closeModal(modal);
}

  openPriceChartModal(){
  var modal=document.getElementById('price-chart-modal');
  if(!modal)return;
  this._openModal(modal,()=>this.closePriceChartModal());
  priceChart.ensureMounted();
  priceChart.hydrateRange(priceChart.settings.range);
  requestAnimationFrame(()=>priceChart.render(true));
}

  closePriceChartModal(){
  var modal=document.getElementById('price-chart-modal');
  if(modal)this._closeModal(modal);
  // Rebuild the preview immediately so it adopts the chart window/range
  // the user just selected, even when the market is closed and no new
  // tick would otherwise trigger a dashboard refresh.
  if(app.chain && typeof app.chain.patchTopBarAndDecision==='function' && typeof _data!=='undefined' && _data){
    app.chain.patchTopBarAndDecision(_data);
  }
}

  // ── GREEKS / GEX MODAL ──
  // Unlike the OI Dashboard modal above, this isn't an iframe to a
  // separate document — the full Greeks/GEX table (renderGreeksGex() in
  // ChainView) already renders straight into #grkgex-content/#grkgex-footer,
  // which now live inside this modal's markup in DashboardPro.html. Closed
  // modals are not rendered on live ticks; opening refreshes from the latest
  // canonical state immediately, avoiding hidden table rebuilds.
  openGreeksModal(){
  var modal = document.getElementById('greeks-dashboard-modal');
  if(!modal) return;
  this._openModal(modal, () => this.closeGreeksModal());
  document.addEventListener('keydown', _greeksEscHandler);
  // Refresh immediately on open too, in case something changed the
  // underlying data without a live tick firing in between (e.g. a
  // paste-load or an expiry switch made while the modal was closed).
  if(window.renderGreeksGex) renderGreeksGex(_grkView);
}

  closeGreeksModal(){
  var modal = document.getElementById('greeks-dashboard-modal');
  if(!modal) return;
  this._closeModal(modal);
  document.removeEventListener('keydown', _greeksEscHandler);
}

  _greeksEscHandler(e){
  if(e.key === 'Escape') closeGreeksModal();
}

  // ── FII / DII MODAL ──
  // Same treatment as the Greeks modal above for the table
  // (#fiidii-modal-content, kept continuously current by ExecView.
  // renderFiiDiiModal() via _rerenderChainPanels regardless of open
  // state) — opening is purely a visibility toggle for that part. Also
  // refreshed right here on open in case something changed the underlying
  // data without a live tick firing in between.
  //
  // The flow/ratio/OI/sector panels below the table (.fd-report, merged
  // in from the old standalone FII-DII.html — see fiidii-report.js) are
  // NOT kept current in the background the way the table is: they're fed by their own WebSocket to /dashboard-relay
  // rather than the main dashboard tick, so FiiDiiReportFeed.connect()/
  // disconnect() start and stop that connection here, on actual open/
  // close, instead of leaving a second live socket running for the whole
  // page lifetime whether or not anyone's looking at this modal.
  openFiiDiiModal(){
  var modal = document.getElementById('fiidii-dashboard-modal');
  if(!modal) return;
  this._openModal(modal, () => this.closeFiiDiiModal());
  document.addEventListener('keydown', _fiidiiEscHandler);
  if(app.data.store.state && app.exec.renderFiiDiiModal) app.exec.renderFiiDiiModal(app.data.store.state);
  if(window.FiiDiiReportFeed) FiiDiiReportFeed.connect();
}

  closeFiiDiiModal(){
  var modal = document.getElementById('fiidii-dashboard-modal');
  if(!modal) return;
  this._closeModal(modal);
  document.removeEventListener('keydown', _fiidiiEscHandler);
  if(window.FiiDiiReportFeed) FiiDiiReportFeed.disconnect();
}

  _fiidiiEscHandler(e){
  if(e.key === 'Escape') closeFiiDiiModal();
}

  // ── IV SURFACE MODAL ──
  // Plain in-page markup (#iv-surface-content) is refreshed only while the
  // modal is visible, plus once immediately on open. openIvSurfaceModal()
  // was already being called by
  // buildIvAlertsHtml()'s "Full Surface →" button, but this method itself
  // (and closeIvSurfaceModal/_ivSurfaceEscHandler) had never actually been
  // written, so that button threw a ReferenceError — root-cause fixed here.
  openIvSurfaceModal(){
  var modal = document.getElementById('iv-surface-modal');
  if(!modal) return;
  this._openModal(modal, () => this.closeIvSurfaceModal());
  document.addEventListener('keydown', _ivSurfaceEscHandler);
  if(typeof app !== 'undefined' && app.chain && app.chain.renderIvSurfaceModal) app.chain.renderIvSurfaceModal();
}

  closeIvSurfaceModal(){
  var modal = document.getElementById('iv-surface-modal');
  if(!modal) return;
  this._closeModal(modal);
  document.removeEventListener('keydown', _ivSurfaceEscHandler);
}

  _ivSurfaceEscHandler(e){
  if(e.key === 'Escape') closeIvSurfaceModal();
}

  // ── STRATEGY PAYOFF EXPAND MODAL ──
  // Unlike the Greeks/FII-DII/IV-Surface modals above (which wrap a
  // separate content block that's kept current independent of the modal's
  // open state), this modal wraps a second canvas
  // (#strat-payoff-canvas-modal) that renderStratPayoff() already paints
  // in the same pass as the inline card's canvas (see
  // _drawPayoffOnCanvas in panels-views.js) — so opening this is purely a
  // visibility toggle, same chrome/Esc/backdrop behavior as the others.
  // Still call renderStratPayoff() once on open in case a tick landed
  // while the modal was closed and the canvas element didn't exist yet
  // for _drawPayoffOnCanvas to find.
  openStratPayoffModal(){
  var modal = document.getElementById('strat-payoff-modal');
  if(!modal) return;
  this._openModal(modal, () => this.closeStratPayoffModal());
  document.addEventListener('keydown', _stratPayoffEscHandler);
  if(window.renderStratPayoff) renderStratPayoff();
}

  closeStratPayoffModal(){
  var modal = document.getElementById('strat-payoff-modal');
  if(!modal) return;
  this._closeModal(modal);
  document.removeEventListener('keydown', _stratPayoffEscHandler);
}

  _stratPayoffEscHandler(e){
  if(e.key === 'Escape') closeStratPayoffModal();
}

  // ── SIMULATOR GEX CHART EXPAND MODAL ──
  // Same treatment as the Strategy Payoff expand modal just above: wraps
  // a second canvas (#sim-gex-canvas-modal) that simRenderGEXChart()
  // already paints in the same pass as the inline card's canvas (see
  // _drawGexOnCanvas in panels-views.js). simUpdate() re-runs the whole
  // simulator calc (stats/regime/GEX chart together), so call that on
  // open rather than the chart draw alone, in case the modal canvas
  // didn't exist yet for the last tick's draw to find.
  openSimGexModal(){
  var modal = document.getElementById('sim-gex-modal');
  if(!modal) return;
  this._openModal(modal, () => this.closeSimGexModal());
  document.addEventListener('keydown', _simGexEscHandler);
  if(window.simUpdate) simUpdate();
}

  closeSimGexModal(){
  var modal = document.getElementById('sim-gex-modal');
  if(!modal) return;
  this._closeModal(modal);
  document.removeEventListener('keydown', _simGexEscHandler);
}

  _simGexEscHandler(e){
  if(e.key === 'Escape') closeSimGexModal();
}

  // ── VOL/OI VELOCITY CHART EXPAND MODAL ──
  // Same treatment as the Strategy Payoff/Net GEX expand modals above:
  // #sdt-voi-grid is repainted by simRenderVolGrid() in the same tick
  // pass as every other simulator panel regardless of whether this modal
  // is open, so it's already current the instant it opens — call
  // simUpdate() on open anyway in case the modal's copy of the element
  // didn't exist yet for the last tick to find (mirrors openSimGexModal).
  openVolOiVelocityModal(){
  var modal = document.getElementById('vol-oi-velocity-modal');
  if(!modal) return;
  this._openModal(modal, () => this.closeVolOiVelocityModal());
  document.addEventListener('keydown', _volOiVelocityEscHandler);
  if(window.simUpdate) simUpdate();
}

  closeVolOiVelocityModal(){
  var modal = document.getElementById('vol-oi-velocity-modal');
  if(!modal) return;
  this._closeModal(modal);
  document.removeEventListener('keydown', _volOiVelocityEscHandler);
}

  _volOiVelocityEscHandler(e){
  if(e.key === 'Escape') closeVolOiVelocityModal();
}

  // ── STRIKE DETAIL REPORT MODAL ──
  // A strike is mandatory: this report investigates one canonical live
  // chain row, while the existing shell continues to own focus/Escape/back.
  openStrikeDetailReportModal(strike){
  var modal = document.getElementById('single-strike-detail-modal');
  var n = Number(strike);
  if(!modal || !Number.isFinite(n)) return false;
  if(!app.strikeDetail.render(n)) return false;
  if(window.eventBus) window.eventBus.emit('strike:select', { strike: n, source: 'strike-detail' });
  this._openModal(modal, () => this.closeStrikeDetailReportModal());
  document.addEventListener('keydown', _strikeDetailReportEscHandler);
  return true;
}

  closeStrikeDetailReportModal(){
  var modal = document.getElementById('single-strike-detail-modal');
  if(!modal) return;
  this._closeModal(modal);
  app.strikeDetail.clear();
  document.removeEventListener('keydown', _strikeDetailReportEscHandler);
}

  _strikeDetailReportEscHandler(e){
  if(e.key === 'Escape') closeStrikeDetailReportModal();
}

  openInstitutionalStrikeReportModal(){
  var modal = document.getElementById('strike-detail-report-modal');
  if(!modal) return false;
  this._openModal(modal, () => this.closeInstitutionalStrikeReportModal());
  if(window.simUpdate) simUpdate();
  return true;
}

  closeInstitutionalStrikeReportModal(){
  var modal = document.getElementById('strike-detail-report-modal');
  if(modal) this._closeModal(modal);
}

  // ── GREEKS BY MONEYNESS CHART EXPAND MODAL ──
  // Same treatment as the Strategy Payoff / Net GEX expand modals above,
  // just Chart.js-based instead of a manual canvas draw: #greeksChart-modal
  // is a second Chart.js instance (ensureGreeksChart('greeksChart-modal')
  // in chart-legend.js) kept current by updateGreeksMoneynessChart() on
  // every render/live tick regardless of open state, so opening this is
  // purely a visibility toggle — same chrome/Esc/backdrop behavior as
  // every other modal here. Re-run the chart update once on open anyway,
  // in case a tick landed before this modal's canvas existed in the DOM
  // for that pass to find (mirrors openSimGexModal/openVolOiVelocityModal
  // re-calling simUpdate() for the same reason).
  openGreeksChartModal(){
  var modal = document.getElementById('greeks-chart-modal');
  if(!modal) return;
  this._openModal(modal, () => this.closeGreeksChartModal());
  document.addEventListener('keydown', _greeksChartEscHandler);
  if(app.data.store.state && window.updateGreeksMoneynessChart){
    window.updateGreeksMoneynessChart(app.data.store.state, true);
  }
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (window.resizeGreeksMoneynessChart) {
      window.resizeGreeksMoneynessChart('greeksChart-modal');
    }
  }));
}

  closeGreeksChartModal(){
  var modal = document.getElementById('greeks-chart-modal');
  if(!modal) return;
  this._closeModal(modal);
  document.removeEventListener('keydown', _greeksChartEscHandler);
}

  _greeksChartEscHandler(e){
  if(e.key === 'Escape') closeGreeksChartModal();
}
}
