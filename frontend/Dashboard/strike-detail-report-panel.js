// ============================================================
// strike-detail-report-panel.js
// Split out of panels-views.js. Now holds just the main dashboard's
// "Full Chain" focus toggle (toggleFullChainFocus) — the Strike Detail
// Report moved out to modal-manager.js's openStrikeDetailReportModal()/
// closeStrikeDetailReportModal() (it used to be a standalone window.open
// popup kept live via postMessage; now it's an in-page .oc-modal like
// every other expand on this dashboard, so it belongs with the rest of
// ModalManager rather than living here as a one-off).
// ============================================================

// ── Vol/OI Velocity chart (main dashboard) ────────────────────────────
// Used to expand inline via a toggle button, growing/shrinking whatever
// sat below it in the page every time someone opened or closed it — see
// openVolOiVelocityModal()/closeVolOiVelocityModal() (ModalManager,
// below) for the click-to-expand modal that replaced it, same treatment
// as the Net GEX Profile and Strategy Payoff charts. #sdt-voi-grid now
// lives only inside that modal but stays in the DOM at all times (never
// display:none'd), so simRenderVolGrid() keeps it current on every tick
// regardless of whether the modal is open — same "already current the
// instant it opens" behavior the GEX/payoff modal canvases have.

  // ── Full Chain inline focus mode (Executive panel) ───────────────────────
// "Full Chain →" used to window.open() the standalone option-chain.html in
// a new tab. Now it's the same show/hide pattern used elsewhere on the page:
// nothing else on the page gets hidden —
// a full-width iframe loading the *same* option-chain.html just gets
// inserted right after the button's own card and shown/hidden on toggle,
// so the chain itself never has two divergent implementations to keep
// in sync.
let _fullChainOpen = false;

function toggleFullChainFocus() {
  const btn = document.getElementById('full-chain-toggle-btn');
  if (!btn) return;

  _fullChainOpen = !_fullChainOpen;

  const ownCard = document.getElementById('chain-summary-card')
    || btn.closest('.exec-card')
    || document.getElementById('exec-section-wrap');
  let frameWrap = document.getElementById('full-chain-frame-wrap');

  if (_fullChainOpen) {
    if (!frameWrap) {
      frameWrap = document.createElement('div');
      frameWrap.id = 'full-chain-frame-wrap';
      frameWrap.style.cssText = 'width:100%;height:80vh;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;margin-top:8px;';
      frameWrap.innerHTML = '<iframe id="full-chain-iframe" src="../OptionChain/option-chain.html" style="width:100%;height:100%;border:0;"></iframe>';
      // #chain-summary-card holds the button itself — inserting right
      // after it keeps the button on top and puts the full chain detail
      // directly below it, regardless of what class the card carries.
      ownCard.insertAdjacentElement('afterend', frameWrap);

      // BUGFIX: the iframe is a separate JS window from this page, so
      // option-chain.js's placeOrder() checking `window._ocPlaceOrder`
      // was always checking the IFRAME's window — which never had this
      // hook set. Every Buy/Sell click inside the embedded chain silently
      // fell through to option-chain.js's own fake confirmation text,
      // with no order ever sent to the backend or recorded in the
      // Order/Trade Log. Set the hook on contentWindow once the iframe
      // has actually loaded (contentWindow isn't ready beforehand), and
      // route it through the same ptDispatchOrder() path every other
      // order source (main form, quick-order popover, strategy legs)
      // uses — translating option-chain.js's payload shape (side=CE/PE,
      // action=BUY/SELL, qty) into paper-trading's (instrument_type,
      // side=BUY/SELL, qty_lots).
      const iframe = frameWrap.querySelector('#full-chain-iframe');
      iframe.addEventListener('load', () => {
        iframe.contentWindow._ocPlaceOrder = (o) => {
          ptDispatchOrder({
            symbol: o.symbol,
            instrument_type: o.side,
            expiry: o.expiry,
            strike: o.strike,
            side: o.action,
            qty_lots: o.qty,
            order_type: 'MARKET',
            limit_price: null
          }, null);
        };
      });
    }
    frameWrap.style.display = '';
    btn.textContent = '← Collapse';
    requestAnimationFrame(() => frameWrap.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  } else {
    if (frameWrap) frameWrap.style.display = 'none';
    btn.textContent = 'Full Chain →';
  }
}
