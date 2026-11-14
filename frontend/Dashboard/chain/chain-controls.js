// Native option-chain disclosure and strike-navigation controls.

// Option-chain controls and navigation stay inside the canonical dashboard. The old
// BroadcastChannel bridge and standalone OptionChain application were
// removed so there is one source of UI state, expiry, range, and orders.

function openOptionChainAtStrike(strike) {
  const n = Number(strike);
  const hasStrike = strike !== null && strike !== '' && Number.isFinite(n);
  if (window.dashboardInstrumentMode === 'commodity') {
    const modal = document.getElementById('option-chain-modal');
    if (modal && !modal.classList.contains('open') && typeof openOptionChainModal === 'function') {
      openOptionChainModal();
    }
    const row = hasStrike
      ? Array.from(document.querySelectorAll('#option-chain-table [data-strike]'))
        .find((item) => Number(item.dataset.strike) === n)
      : null;
    if (row) {
      document.querySelectorAll('#option-chain-table [data-strike].focused')
        .forEach((item) => item.classList.remove('focused'));
      row.classList.add('focused');
      row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else {
      document.getElementById('commodity-option-chain')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    return false;
  }
  if (hasStrike && window.openStrikeDetailReportModal) {
    window.openStrikeDetailReportModal(n);
    return false;
  }
  const card = document.getElementById('chain-summary-card');
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const header = card.querySelector('.section-header');
    if (header && typeof header.focus === 'function') header.focus({ preventScroll: true });
  }
  return false;
}

function toggleOptionChainSnapshot(button) {
  if (typeof openOptionChainModal === 'function') openOptionChainModal(button);
  return false;
}

function setOptionChainLedgerView(view, button) {
  const allowed = new Set(['positioning', 'activity', 'greeks', 'all']);
  if (!allowed.has(view)) return;
  const table = document.querySelector('#option-chain-table .oc-ledger-table');
  if (table) table.dataset.view = view;
  document.querySelectorAll('[data-chain-view]').forEach((control) => {
    const selected = control.dataset.chainView === view;
    control.classList.toggle('active', selected);
    control.setAttribute('aria-pressed', String(selected));
  });
  if (typeof app !== 'undefined' && app.chain) app.chain.chainLedgerView = view;
  if (typeof app !== 'undefined' && app.modal && typeof app.modal.renderOptionChainContext === 'function' && typeof _data !== 'undefined') {
    app.modal.renderOptionChainContext(_data);
  }
}
