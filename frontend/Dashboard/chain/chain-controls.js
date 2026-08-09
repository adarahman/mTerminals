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

function openOptionChain() {
  return openOptionChainAtStrike(null);
}

function toggleOptionChainSnapshot(button) {
  const card = button && button.closest('#chain-summary-card');
  const table = card && card.querySelector('.oc-native-chain');
  if (!table) return false;
  const expanded = button.getAttribute('aria-expanded') !== 'false';
  const nextExpanded = !expanded;
  button.setAttribute('aria-expanded', String(nextExpanded));
  table.hidden = !nextExpanded;
  const arrow = button.querySelector('.nav-card-header-arrow');
  if (arrow) arrow.textContent = nextExpanded ? '▾' : '↗';
  if (typeof app !== 'undefined' && app.chain) {
    app.chain.chainTableOpen = nextExpanded;
  }
  if (nextExpanded) table.scrollIntoView({ behavior:'smooth', block:'nearest' });
  return false;
}

function toggleOptionChainGreeks(button) {
  const card = button && button.closest('#chain-summary-card');
  const visible = button.getAttribute('aria-pressed') !== 'true';
  button.setAttribute('aria-pressed', String(visible));
  button.classList.toggle('active', visible);
  card && card.querySelectorAll('.oc-ledger-greeks').forEach((row) => {
    row.hidden = !visible;
  });
  if (typeof app !== 'undefined' && app.chain) app.chain.chainGreeksVisible = visible;
}
