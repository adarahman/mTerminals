// Native option-chain disclosure and strike-navigation controls.

// Option-chain controls and navigation stay inside the canonical dashboard. The old
// BroadcastChannel bridge and standalone OptionChain application were
// removed so there is one source of UI state, expiry, range, and orders.

function openOptionChainAtStrike(strike) {
  const n = Number(strike);
  const hasStrike = strike !== null && strike !== '' && Number.isFinite(n);
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
}
