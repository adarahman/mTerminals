// ── order-entry.js ──────────────────────────────────────────────────────
// Split 2026-08-05 out of the old monolithic paper-trading.js. Owns
// #pt-order-panel end to end: the order form itself, the basket, the
// option-chain "quick order" popover, and strategy/decision-engine leg
// execution. Depends on paper-trading-shared.js (load it first) for
// constants/formatting, lot sizes, LTP-independent helpers, the order
// dispatch pipeline (ptDispatchOrder), and panel open/close mechanics.
// portfolio-tracker.js depends on this file for ptSyncFormFromWsState()
// (called from its renderPaperTradingPanel orchestrator) and for
// ptMountOrderPanel() (called from its ptMountPanel orchestrator).

// True once the user has explicitly changed the Symbol dropdown
// themselves — before that, renderPaperTradingPanel() is free to keep
// syncing it to whatever symbol the WS is actually streaming (see the
// mount-race bugfix below: the panel mounts on DOMContentLoaded, which
// fires before connectWebSocket()'s first tick, so at mount time there is
// no live symbol to prefill from yet).
let _ptSymbolTouched = false;
let _ptSymbolUniverseSig = '';

// Mirrors the main dashboard symbol picker: consume the backend-supplied
// complete F&O universe, group indices and stocks, and preserve whichever
// value the order form currently owns. The small static list is only a
// first-paint fallback before fnoSymbols arrives on the live payload.
function ptPopulateOrderSymbols(fnoSymbols, preferredSymbol){
  const select = $i('pt-symbol');
  if(!select) return;
  const indices = Array.isArray(fnoSymbols && fnoSymbols.indices) ? fnoSymbols.indices : PT_KNOWN_SYMBOLS;
  const stocks = Array.isArray(fnoSymbols && fnoSymbols.stocks) ? fnoSymbols.stocks : [];
  const preferred = preferredSymbol || select.value || indices[0] || '';
  const normalizedIndices = indices.includes(preferred) || stocks.includes(preferred)
    ? indices : [preferred, ...indices].filter(Boolean);
  const signature = normalizedIndices.join('|') + '::' + stocks.join('|');
  if(signature === _ptSymbolUniverseSig){
    if(preferred && Array.from(select.options).some(o=>o.value===preferred)) select.value=preferred;
    return;
  }
  _ptSymbolUniverseSig = signature;
  select.innerHTML = '';
  const appendGroup = (label, symbols) => {
    if(!symbols.length) return;
    const group = document.createElement('optgroup');
    group.label = label;
    symbols.forEach(sym=>group.appendChild(new Option(sym, sym)));
    select.appendChild(group);
  };
  appendGroup('Indices', normalizedIndices);
  appendGroup('Stocks', stocks);
  if(preferred && Array.from(select.options).some(o=>o.value===preferred)) select.value=preferred;
}

// Builds #pt-order-panel's DOM and wires every control inside it. Called
// once by portfolio-tracker.js's ptMountPanel() orchestrator (which also
// calls ptMountSharedHosts() and ptMountPortfolioPanel() — see that file).
// Styling for #pt-order-panel/#pt-quick-popover/.pt-toast/#pt-live-confirm-*
// lives in styles/paper-trading.css (was runtime-injected here via a
// <style> tag).
//
// The toggle button itself is not created here — it's the static
// #pt-order-toggle-btn "Order" sec-btn in the left #sec-nav-bar rail
// (DashboardPro.html). Split 2026-08-04 from a single combined "Paper"
// button/panel (itself relocated 2026-08-02 from a floating fixed
// bottom-right button) into two: Order GUI (this panel) and Portfolio
// Tracker (P&L, Fund, positions, trade log — portfolio-tracker.js).
// toggleOrderPanel() (below) opens this panel next to its rail button.
function ptMountOrderPanel(){
  const orderPanel = document.createElement('div');
  orderPanel.id = 'pt-order-panel';
  orderPanel.innerHTML = `
    <h4 class="pt-order-header"><span id="pt-panel-title">Order Entry</span> <button type="button" id="pt-mode-toggle" class="pt-mode-toggle paper" onclick="ptToggleLiveMode()" title="Switch between Paper and Live trading">📝 PAPER</button> <button type="button" class="pt-close" onclick="ptClosePanel('pt-order-panel','pt-order-toggle-btn')" aria-label="Close order panel">✕</button></h4>
    <div class="pt-section pt-order-form">
      <div class="pt-row">
        <select id="pt-symbol"></select>
        <div class="pt-toggle-group pt-asset-toggle" id="pt-asset-toggle" role="group" aria-label="Instrument class">
          <button type="button" class="pt-toggle-btn" data-value="EQ">EQ</button>
          <button type="button" class="pt-toggle-btn" data-value="FUT">FUT</button>
          <button type="button" class="pt-toggle-btn active" data-value="OPT">OPT</button>
        </div>
        <select id="pt-asset-class" style="display:none;"><option value="EQ">EQ</option><option value="FUT">FUT</option><option value="OPT" selected>OPT</option></select>
        <select id="pt-instype" style="display:none;">
          <option value="CE" selected>CE</option><option value="PE">PE</option>
          <option value="FUT">FUT</option><option value="INDEX">EQ</option>
        </select>
      </div>
      <div class="pt-row pt-option-type-row" id="pt-option-type-row">
        <div class="pt-toggle-group" id="pt-option-type-toggle" role="group" aria-label="Option type">
          <button type="button" class="pt-toggle-btn pt-toggle-ce active" data-value="CE">CE</button>
          <button type="button" class="pt-toggle-btn pt-toggle-pe" data-value="PE">PE</button>
        </div>
      </div>
      <div class="pt-row">
        <select id="pt-expiry"><option value="">Expiry…</option></select>
        <select id="pt-strike"><option value="">Strike…</option></select>
      </div>
      <div class="pt-row">
        <div class="pt-toggle-group" id="pt-side-toggle" role="group" aria-label="Side">
          <button type="button" class="pt-toggle-btn pt-toggle-buy active" data-value="BUY">BUY</button>
          <button type="button" class="pt-toggle-btn pt-toggle-sell" data-value="SELL">SELL</button>
        </div>
        <select id="pt-side" style="display:none;"><option value="BUY">BUY</option><option value="SELL">SELL</option></select>
        <input id="pt-qty" type="number" min="1" value="1" placeholder="Lots">
      </div>
      <div id="pt-lotsize-hint" class="pt-order-hint">Lot size: — · Total qty: —</div>
      <div class="pt-row">
        <div class="pt-toggle-group" id="pt-ordtype-toggle" role="group" aria-label="Order type">
          <button type="button" class="pt-toggle-btn active" data-value="MARKET">MARKET</button>
          <button type="button" class="pt-toggle-btn" data-value="LIMIT">LIMIT</button>
        </div>
        <select id="pt-ordtype" style="display:none;">
          <option value="MARKET">MARKET</option><option value="LIMIT">LIMIT</option>
        </select>
      </div>
      <div class="pt-row" id="pt-limitprice-row">
        <input id="pt-limitprice" type="number" placeholder="Limit price" disabled>
      </div>
      <div class="pt-row" id="pt-trigger-row" style="display:none;">
        <div class="pt-toggle-group" id="pt-trigger-mode-toggle" role="group" aria-label="Trigger mode" style="flex:0 0 auto;">
          <button type="button" class="pt-toggle-btn active" data-value="abs" title="Enter trigger as an absolute price">₹</button>
          <button type="button" class="pt-toggle-btn" data-value="pct" title="Enter trigger as % offset from current LTP">%</button>
        </div>
        <select id="pt-trigger-mode" style="display:none;"><option value="abs">abs</option><option value="pct">pct</option></select>
        <input id="pt-trigger-price" type="number" placeholder="Trigger price">
      </div>
      <div id="pt-trigger-pct-hint" class="pt-order-hint" style="display:none;"></div>
      <div class="pt-row" id="pt-trail-row" style="display:none;">
        <input id="pt-trail-value" type="number" min="0.05" step="0.05" placeholder="Trail by (points)">
      </div>
      <div class="pt-row" id="pt-gtt-row" style="display:none;">
        <input id="pt-gtt-expiry" type="number" min="1" value="30" placeholder="GTT valid for (days)">
      </div>
      <div id="pt-ltp-hint" class="pt-order-hint pt-order-ltp">LTP: —</div>
      <div class="pt-row" id="pt-submit-row">
      </div>
      <div id="pt-err"></div>
    </div>
  `;
  document.body.appendChild(orderPanel);

  ptPopulateOrderSymbols(AppState.wsState && AppState.wsState.fnoSymbols,
    AppState.wsState && AppState.wsState.symbol);

  // Each order type needs a different subset of the four extra fields
  // (limit price / trigger price / trail value / GTT expiry). Rather than
  // one boolean (LIMIT vs not), map each type to exactly which rows it
  // needs so adding a 5th type later is a one-line change here.
  const PT_ORDTYPE_FIELDS = {
    'MARKET': [],
    'LIMIT':  ['limitprice'],
    'SL':     ['trigger', 'limitprice'],   // stop-loss LIMIT: trigger fires, then rests as a limit order
    'SL-M':   ['trigger'],                 // stop-loss MARKET: trigger fires, then fills at market
    'TSL':    ['trail'],                   // trailing stop-loss: trigger recalculated server-side as price moves
    'GTT':    ['trigger', 'limitprice', 'gtt'],
  };
  function ptUpdateOrdTypeFields(){
    const type = $i('pt-ordtype').value;
    const need = PT_ORDTYPE_FIELDS[type] || [];
    $i('pt-limitprice-row').style.display = need.includes('limitprice') ? '' : 'none';
    $i('pt-trigger-row').style.display    = need.includes('trigger')    ? '' : 'none';
    $i('pt-trail-row').style.display      = need.includes('trail')      ? '' : 'none';
    $i('pt-gtt-row').style.display        = need.includes('gtt')        ? '' : 'none';
    $i('pt-limitprice').disabled = !need.includes('limitprice');
  }
  $i('pt-ordtype').onchange = ()=>{
    ptUpdateOrdTypeFields();
    ptUpdateLtpHint();
  };
  // Click-toggle groups for Side (BUY/SELL) and Order Type (MARKET/LIMIT):
  // clicking a button sets the paired hidden <select>'s value, updates
  // which button shows as active, and dispatches a real 'change' event so
  // the existing onchange handler above (and anything else reading
  // $i('pt-side')/$i('pt-ordtype').value) keeps working exactly as if the
  // user had picked it from the dropdown.
  function ptWireToggleGroup(groupId, selectId){
    const group = $i(groupId);
    if(!group) return;
    group.addEventListener('click', (e)=>{
      const btn = e.target.closest('.pt-toggle-btn');
      if(!btn || btn.classList.contains('active')) return;
      group.querySelectorAll('.pt-toggle-btn').forEach(b=>b.classList.toggle('active', b===btn));
      const sel = $i(selectId);
      sel.value = btn.dataset.value;
      sel.dispatchEvent(new Event('change'));
    });
  }
  function ptUpdateAssetClassUi(){
    const assetClass = $i('pt-asset-class').value;
    const optionRow = $i('pt-option-type-row');
    const instype = $i('pt-instype');
    optionRow.style.display = assetClass === 'OPT' ? '' : 'none';
    if(assetClass === 'OPT'){
      const activeOption = $i('pt-option-type-toggle').querySelector('.pt-toggle-btn.active');
      instype.value = activeOption ? activeOption.dataset.value : 'CE';
    } else {
      // The backend prices the cash underlying under its historical INDEX
      // instrument key; the visible trader-facing label remains EQ.
      instype.value = assetClass === 'FUT' ? 'FUT' : 'INDEX';
    }
    instype.dispatchEvent(new Event('change'));
  }
  ptWireToggleGroup('pt-side-toggle', 'pt-side');
  ptWireToggleGroup('pt-asset-toggle', 'pt-asset-class');
  ptWireToggleGroup('pt-option-type-toggle', 'pt-instype');
  ptWireToggleGroup('pt-ordtype-toggle', 'pt-ordtype');
  ptWireToggleGroup('pt-trigger-mode-toggle', 'pt-trigger-mode');
  $i('pt-trigger-mode').onchange = ptUpdateTriggerModeUi;
  $i('pt-trigger-price').addEventListener('input', ptUpdateTriggerPctHint);
  $i('pt-asset-class').onchange = ptUpdateAssetClassUi;
  $i('pt-instype').onchange = ptRefreshExpiryStrikeOptions;
  $i('pt-expiry').onchange  = ptRefreshStrikeOptions;
  $i('pt-strike').onchange  = ptUpdateLtpHint;
  $i('pt-symbol').onchange  = ()=>{ _ptSymbolTouched = true; ptRefreshExpiryStrikeOptions(); ptUpdateLotSizeHint(); };
  $i('pt-qty').addEventListener('input', ptUpdateLotSizeHint);

  // First live call site for MTButton (components/mt-button.js) — see
  // DESIGN_SYSTEM.md section 20. Replaces the static `<button
  // id="pt-submit-btn">` that used to sit in the innerHTML template
  // above; #pt-submit-row is the empty slot left for it. Kept the same
  // id and the same flex:2 sizing so nothing else in this file (or
  // paper-trading.css) needs to change. setError() below gives failed
  // submits a real button-level state instead of only the small #pt-err
  // line underneath — see ptSubmitOrder()/_ptSendOrderNow()'s new `btn`
  // param.
  const submitBtn = MTButton({ id: 'pt-submit-btn', label: 'Place Order', onClick: ptSubmitOrder });
  submitBtn.style.flex = '2';
  $i('pt-submit-row').prepend(submitBtn);

  // Prefill symbol/expiry/ATM strike from whatever the dashboard is
  // currently showing, so the common case (order the ATM strike on the
  // active scrip/expiry) needs zero typing — and populate the expiry/
  // strike <select>s from real chain data instead of leaving them as
  // free-typed text boxes.
  if(AppState.wsState && AppState.wsState.symbol) $i('pt-symbol').value = AppState.wsState.symbol;
  ptRefreshExpiryStrikeOptions();
  ptRefreshLotSizes();
  ptUpdateLotSizeHint();
  ptUpdateOrdTypeFields();
  ptUpdateAssetClassUi();
}

function toggleOrderPanel(){
  ptTogglePanelNear('pt-order-panel', 'pt-order-toggle-btn');
}
window.toggleOrderPanel = toggleOrderPanel;

// Rebuilds the expiry <select> (and, via ptRefreshStrikeOptions, the
// strike <select>) from live chain data instead of requiring manual
// typing. Only the symbol the backend is actually streaming
// (AppState.wsState.symbol) has expiry/strike data available client-side; for
// any other symbol picked in pt-symbol the dropdowns are left disabled
// with a note, since there's no chain to source options from.
function ptRefreshExpiryStrikeOptions(){
  const instype = $i('pt-instype').value;
  const symbol  = $i('pt-symbol').value;
  const expSel  = $i('pt-expiry');
  const needsExpiry = instype === 'CE' || instype === 'PE' || instype === 'FUT';
  const needsStrike = instype === 'CE' || instype === 'PE';

  const sameSymbol = AppState.wsState && AppState.wsState.symbol === symbol;
  const chainStore = (sameSymbol && AppState.wsState.chains) || {};
  let expiries = Object.keys(chainStore);
  if(!expiries.length && sameSymbol && (AppState.wsState.expiry || (AppState.wsState._activeExpiry))) {
    expiries = [AppState.wsState._primaryExpiry || AppState.wsState.expiry];
  }

  const prevExpiry = expSel.value;
  expSel.innerHTML = '';
  if(!needsExpiry){
    expSel.appendChild(new Option('N/A', ''));
    expSel.disabled = true;
  } else if(!expiries.length){
    expSel.appendChild(new Option('No data — switch to ' + (symbol||'this symbol') + ' first', ''));
    expSel.disabled = true;
  } else {
    expSel.disabled = false;
    expiries.forEach(exp=> expSel.appendChild(new Option(exp, exp)));
    expSel.value = expiries.includes(prevExpiry) ? prevExpiry
      : (expiries.includes(_selectedExpiry) ? _selectedExpiry : expiries[0]);
  }
  $i('pt-strike').disabled = !needsStrike;
  ptRefreshStrikeOptions();
}

function ptRefreshStrikeOptions(){
  const instype = $i('pt-instype').value;
  const strikeSel = $i('pt-strike');
  if(instype !== 'CE' && instype !== 'PE'){
    strikeSel.innerHTML = '';
    strikeSel.appendChild(new Option('N/A', ''));
    ptUpdateLtpHint();
    return;
  }
  const symbol = $i('pt-symbol').value;
  const expiry = $i('pt-expiry').value;
  const sameSymbol = AppState.wsState && AppState.wsState.symbol === symbol;
  let rows = [];
  if(sameSymbol){
    if(AppState.wsState.chains && AppState.wsState.chains[expiry]) rows = AppState.wsState.chains[expiry];
    else if(expiry && expiry === AppState.wsState.expiry) rows = AppState.wsState.chain || [];
  }
  const prevStrike = strikeSel.value;
  strikeSel.innerHTML = '';
  if(!rows.length){
    strikeSel.appendChild(new Option('No strikes — pick an expiry', ''));
    ptUpdateLtpHint();
    return;
  }
  const strikes = rows.map(r=>r.strike).sort((a,b)=>a-b);
  strikes.forEach(sk=> strikeSel.appendChild(new Option(fmtI(sk), sk)));
  const atm = sameSymbol ? activeAtm(AppState.wsState) : null;
  const keep = strikes.map(String).includes(prevStrike) ? prevStrike
    : (atm && strikes.includes(atm) ? String(atm) : String(strikes[0]));
  strikeSel.value = keep;
  ptUpdateLtpHint();
}

// Looks up the live LTP for whatever the form currently has selected and
// (a) shows it in the "LTP:" hint line, and (b) auto-fills the limit
// price field so LIMIT orders don't require the price to be typed by
// hand. Only overwrites the price if the user hasn't already typed
// something different from the last value we auto-filled, so manual
// overrides aren't clobbered on the next tick.
let _ptLastAutoLimit = null;
// Shared LTP lookup, usable both by the main panel form (via ptFindLiveLtp,
// which reads the pt-* <select> values) and by the quick popover / strategy
// leg execution (which have their own symbol/expiry/strike already in
// hand). Centralizing this means every order path checks price
// availability the same way instead of each guessing independently.
function ptResolveLtp(symbol, instrument_type, expiry, strike){
  if(!(AppState.wsState && AppState.wsState.symbol === symbol)) return null;
  if(instrument_type === 'INDEX') return parseFloat(AppState.wsState.spot) || null;
  if(instrument_type === 'FUT') return parseFloat(AppState.wsState.futLTP || AppState.wsState.spot) || null;
  if(!expiry || strike == null || isNaN(strike)) return null;
  let rows = (AppState.wsState.chains && AppState.wsState.chains[expiry]) ? AppState.wsState.chains[expiry]
    : (expiry === AppState.wsState.expiry ? (AppState.wsState.chain||[]) : []);
  const row = rows.find(r=>r.strike===strike);
  if(!row) return null;
  const v = instrument_type==='CE' ? row.ceLTP : row.peLTP;
  return (v === null || v === undefined || isNaN(v)) ? null : v;
}

function ptFindLiveLtp(){
  const instype = $i('pt-instype').value;
  const symbol  = $i('pt-symbol').value;
  const expiry  = $i('pt-expiry').value;
  const strike  = parseFloat($i('pt-strike').value);
  return ptResolveLtp(symbol, instype, expiry, strike);
}

// Shows the exchange-fixed lot size for whatever symbol is currently
// selected, plus the actual quantity (lot size × lots) the order form
// will submit — mirrors what NSE/BSE broker terminals show next to the
// "Qty" field so it's clear "3 lots" of BANKNIFTY (lot 35) means 105
// units, not 3.
function ptUpdateLotSizeHint(){
  const hint = $i('pt-lotsize-hint');
  if(!hint) return;
  const symbol = $i('pt-symbol').value;
  const lot = ptGetLotSize(symbol);
  const lots = parseInt($i('pt-qty').value, 10);
  if(lot == null){
    hint.textContent = 'Lot size: resolving… · Total qty: —';
    return;
  }
  const totalQty = (lots > 0) ? lot * lots : null;
  hint.textContent = 'Lot size: ' + lot + ' · Total qty: ' + (totalQty != null ? totalQty : '—');
}

function ptUpdateLtpHint(){
  const ltp = ptFindLiveLtp();
  const hint = $i('pt-ltp-hint');
  if(hint) hint.textContent = 'LTP: ' + (ltp!=null ? ptFmtN(ltp,2) : '—');
  const priceInput = $i('pt-limitprice');
  const type = $i('pt-ordtype').value;
  if(priceInput && ltp!=null && (type==='LIMIT' || type==='SL' || type==='GTT')){
    const cur = priceInput.value;
    if(cur === '' || parseFloat(cur) === _ptLastAutoLimit){
      priceInput.value = ltp;
      _ptLastAutoLimit = ltp;
    }
  }
  ptUpdateTriggerPctHint();
}

// Trigger price can be entered either as an absolute price (₹) or as a %
// offset from the current LTP (e.g. -2 = 2% below LTP, useful for a long
// SL, or +2 = 2% above, useful for a short SL) — SL, SL-M and GTT all share
// the same pt-trigger-price input, just switching what its number means.
// Swapping the placeholder/hint here, and resolving to an actual rupee
// price in ptResolveTriggerPrice(), keeps the % option from requiring any
// change to what gets sent to the backend (still a plain trigger_price).
function ptUpdateTriggerModeUi(){
  const mode = $i('pt-trigger-mode').value;
  const input = $i('pt-trigger-price');
  input.placeholder = mode === 'pct' ? '% offset from LTP (e.g. -2 or 2)' : 'Trigger price';
  ptUpdateTriggerPctHint();
}
function ptUpdateTriggerPctHint(){
  const mode = $i('pt-trigger-mode').value;
  const hint = $i('pt-trigger-pct-hint');
  if(mode !== 'pct'){ hint.style.display = 'none'; return; }
  const pct = parseFloat($i('pt-trigger-price').value);
  const ltp = ptFindLiveLtp();
  if(isNaN(pct) || ltp == null){
    hint.style.display = 'none';
    return;
  }
  const price = ltp * (1 + pct / 100);
  hint.textContent = '≈ ' + ptFmtN(price, 2) + ' (LTP ' + ptFmtN(ltp, 2) + ' ' + (pct >= 0 ? '+' : '') + pct + '%)';
  hint.style.display = '';
}
// Resolves whatever's in pt-trigger-price to an absolute rupee price,
// regardless of which mode (abs/pct) is currently selected. Returns null
// if the value can't be resolved (empty, NaN, or % mode with no live LTP
// yet) so callers can surface a clear validation error instead of sending
// a bad/zero trigger.
function ptResolveTriggerPrice(){
  const raw = $i('pt-trigger-price').value;
  if(raw === '') return null;
  const val = parseFloat(raw);
  if(isNaN(val)) return null;
  if($i('pt-trigger-mode').value !== 'pct') return val;
  const ltp = ptFindLiveLtp();
  if(ltp == null) return null;
  return Math.round(ltp * (1 + val / 100) * 100) / 100;
}

// Reads and validates the single-leg MARKET/LIMIT order form.
function ptGatherOrderFromForm(){
  const symbol = $i('pt-symbol').value;
  const instrument_type = $i('pt-instype').value;
  const expiry = $i('pt-expiry').value.trim();
  const strikeRaw = $i('pt-strike').value;
  const strike = strikeRaw === '' ? null : parseFloat(strikeRaw);
  const side = $i('pt-side').value;
  const qty_lots = parseInt($i('pt-qty').value, 10);
  const order_type = $i('pt-ordtype').value;
  const limitRaw = $i('pt-limitprice').value;
  const limit_price = limitRaw === '' ? null : parseFloat(limitRaw);
  const trigger_price = ptResolveTriggerPrice();
  const trailRaw = $i('pt-trail-value').value;
  const trail_value = trailRaw === '' ? null : parseFloat(trailRaw);
  const gttRaw = $i('pt-gtt-expiry').value;
  const gtt_expiry_days = gttRaw === '' ? null : parseInt(gttRaw, 10);

  if((instrument_type === 'CE' || instrument_type === 'PE') && (!expiry || strike === null)){
    return { error: 'Expiry + strike required for CE/PE' };
  }
  if(!qty_lots || qty_lots <= 0){
    return { error: 'Qty (lots) must be > 0' };
  }
  if(order_type === 'LIMIT'
     && (limit_price === null || isNaN(limit_price))){
    return { error: `Limit price required for ${order_type} orders` };
  }
  if(order_type !== 'MARKET' && order_type !== 'LIMIT'){
    return { error: `${order_type} is not supported by the paper simulator` };
  }

  // This is the actual fix for "price not picking, order being rejected":
  // MARKET orders never carry a client-side price (the server is meant to
  // price the fill off its own latest tick) — but if the option chain
  // simply hasn't delivered an LTP yet for this exact expiry/strike (fresh
  // page load, symbol just switched, illiquid/far strike, or a mismatch
  // between the selected symbol and what the backend is currently
  // streaming), the order was going out anyway and coming back rejected
  // with no clear reason. Catch that here, before it's sent, with an
  // explanation instead of a silent round-trip failure.
  if(order_type === 'MARKET' && ptFindLiveLtp() == null){
    return { error: 'No live price yet for this instrument — order not sent. Wait a moment for the next tick, or switch to LIMIT and enter a price.' };
  }

  // No client-side price is ever sent for MARKET orders — the WS handler
  // in ws_server_live.py is expected to price the fill off the SAME tick's
  // option chain / futures / spot LTP server-side (per place_order()'s
  // current_ltp param), so the panel only sends order intent.
  //
  const order = { symbol, instrument_type, expiry, strike, side, qty_lots, order_type, limit_price };
  return { order };
}

function ptSubmitOrder(){
  const errEl = $i('pt-err');
  errEl.style.color = 'var(--neg,#e74c3c)';
  errEl.textContent = '';
  const { order, error } = ptGatherOrderFromForm();
  if(error){ errEl.textContent = error; return; }
  ptDispatchOrder(order, errEl, $i('pt-submit-btn'));
}

// Opens a small BUY/SELL popover anchored to the LTP cell that was
// clicked in the option chain, so an order can be placed against that
// exact strike without ever touching the main panel's dropdowns. The
// popover host (#pt-quick-popover) plus its click-away/Escape dismissal
// are mounted once by paper-trading-shared.js's ptMountSharedHosts().
function ptOpenQuickOrder(evt, strike, instrument_type, ltp){
  evt.stopPropagation();
  const pop = $i('pt-quick-popover');
  if(!pop || !AppState.wsState) return;
  const symbol = AppState.wsState.symbol || '';
  const expiry = AppState.wsState._activeExpiry || _selectedExpiry || AppState.wsState.expiry || '';
  pop.innerHTML = `
    <div class="pt-qp-hdr"><span>${symbol} ${fmtI(strike)} ${instrument_type}</span><button type="button" class="pt-qp-close" onclick="$i('pt-quick-popover').style.display='none'" aria-label="Close quick order">✕</button></div>
    <div class="pt-qp-ltp">LTP: ${ltp!=null ? ptFmtN(ltp,2) : '—'} &nbsp;|&nbsp; ${expiry||'—'}</div>
    <div class="pt-qp-row">
      <input id="pt-qp-qty" type="number" min="1" value="1" placeholder="Lots">
      <select id="pt-qp-ordtype"><option value="MARKET">MARKET</option><option value="LIMIT">LIMIT</option></select>
    </div>
    <div id="pt-qp-lotsize-hint" style="font-size:10px;opacity:.65;margin:2px 0 4px;"></div>
    <div class="pt-qp-row" id="pt-qp-pricerow" style="display:none;">
      <input id="pt-qp-price" type="number" placeholder="Limit price" value="${ltp!=null?ltp:''}">
    </div>
    <div class="pt-qp-btns">
      <button class="pt-qp-buy" onclick="ptQuickSubmit('BUY',${strike},'${instrument_type}','${expiry}')">BUY</button>
      <button class="pt-qp-sell" onclick="ptQuickSubmit('SELL',${strike},'${instrument_type}','${expiry}')">SELL</button>
    </div>
  `;
  $i('pt-qp-ordtype').onchange = (e)=>{ $i('pt-qp-pricerow').style.display = e.target.value==='LIMIT' ? 'flex' : 'none'; };
  // Same lot-size/total-qty hint as the main panel, keyed off the symbol
  // this popover was opened for (not the main panel's pt-symbol, which
  // may point at a different scrip).
  function ptQpUpdateLotSizeHint(){
    const lot = ptGetLotSize(symbol);
    const lots = parseInt($i('pt-qp-qty').value, 10);
    if(lot == null){
      $i('pt-qp-lotsize-hint').textContent = 'Lot size: resolving… · Total qty: —';
      return;
    }
    const totalQty = (lots > 0) ? lot * lots : null;
    $i('pt-qp-lotsize-hint').textContent = 'Lot size: ' + lot + ' · Total qty: ' + (totalQty != null ? totalQty : '—');
  }
  $i('pt-qp-qty').addEventListener('input', ptQpUpdateLotSizeHint);
  ptQpUpdateLotSizeHint();
  // Position near the click, clamped so it never spills off-screen.
  const pad = 12;
  let x = evt.clientX + 10, y = evt.clientY + 10;
  const w = 190, h = 160;
  if(x + w + pad > window.innerWidth) x = window.innerWidth - w - pad;
  if(y + h + pad > window.innerHeight) y = window.innerHeight - h - pad;
  pop.style.left = Math.max(pad,x) + 'px';
  pop.style.top = Math.max(pad,y) + 'px';
  pop.style.display = 'block';
}
window.ptOpenQuickOrder = ptOpenQuickOrder;

function ptQuickSubmit(side, strike, instrument_type, expiry){
  const qty_lots = parseInt($i('pt-qp-qty').value, 10);
  const order_type = $i('pt-qp-ordtype').value;
  const limitRaw = $i('pt-qp-price').value;
  const limit_price = limitRaw === '' ? null : parseFloat(limitRaw);
  if(!qty_lots || qty_lots <= 0){ ptToast('Qty (lots) must be > 0', 'err'); return; }
  if(order_type==='LIMIT' && (limit_price===null || isNaN(limit_price))){ ptToast('Limit price required', 'err'); return; }
  // Same guard as the main panel: don't send a MARKET order the backend
  // has no price to fill, re-checked live (not the possibly-stale LTP the
  // popover was opened with) since some time may have passed while typing.
  if(order_type==='MARKET' && ptResolveLtp(AppState.wsState.symbol, instrument_type, expiry, strike) == null){
    ptToast('No live price yet for this strike — order not sent', 'err');
    return;
  }
  const payload = {
    symbol: AppState.wsState.symbol, instrument_type, expiry, strike, side,
    qty_lots, order_type, limit_price
  };
  ptDispatchOrder(payload, null);
  $i('pt-quick-popover').style.display = 'none';
}
window.ptQuickSubmit = ptQuickSubmit;

// Calendar-spread legs are labeled "NEAR"/"FAR" (see the leg pill display
// and the Order/Trade Log's Expiry column) rather than a real date — that's
// fine for display, but it's exactly why "current LTP required, FAR and
// NEAR both" MARKET orders were getting rejected: the label "FAR"/"NEAR"
// was being sent as-is in the order payload and used as-is for the live
// LTP lookup, and neither the option chain (AppState.wsState.chain/.chains) nor
// the backend engine has any entry keyed by the literal string "FAR" or
// "NEAR" — only real "DD-Mon-YYYY" dates. This resolves the label to an
// actual expiry date (nearest available for NEAR, farthest available for
// FAR) before it's ever used for pricing or sent to the server. Real date
// strings pass through untouched.
function ptResolveStrategyExpiry(expiry){
  if(!expiry) return expiry;
  const norm = String(expiry).trim().toUpperCase();
  if(!AppState.wsState) return expiry;
  const dates = (AppState.wsState.expiryDates && AppState.wsState.expiryDates.length)
    ? AppState.wsState.expiryDates
    : Object.keys(AppState.wsState.chains || {});
  if(!dates.length) return expiry;
  // renderExpiryOptions() builds the expiry <select> by iterating
  // expiryDates in the order the backend sends them — i.e. chronological,
  // nearest first — so first/last here mirrors that same assumption.
  if(norm === 'NEAR' || norm === 'FAR'){
    return norm === 'NEAR' ? dates[0] : dates[dates.length - 1];
  }
  // BUGFIX: real "DD-Mon-YYYY" dates used to be passed straight through
  // untouched on the assumption that a real date is always safe to send
  // as-is. That's false once you account for where these dates come
  // from: _data.strategies is a client-cached strategy suggestion,
  // generated once and re-rendered on every tick without being
  // regenerated — so s.expiry/l.expiry can go stale the moment the
  // front-month expiry rolls (e.g. "24-Jun" is still sitting in a cached
  // suggestion after 24-Jun has actually expired). Sending that straight
  // to the backend gets a hard REJECTED ("abrupt expiry") instead of
  // trading the leg at all, while the live expiry (e.g. "14-Jul") the
  // user actually sees on the chain works fine. Guard against that by
  // checking the date is still one of the currently listed expiries;
  // if it's rolled off, fall back to the nearest live expiry instead of
  // trusting a contract the live chain no longer recognizes.
  if(!dates.includes(expiry)){
    return dates[0];
  }
  return expiry;
}

// Lets the Strategy Payoff panel place orders too — one leg, or the whole
// strategy at once. Both route through ptDispatchOrder() so toasts,
// pending rows, and the portfolio/orders refresh behave exactly like an
// order placed from the main panel or the option-chain quick popover.
// No client-side LTP is sent for MARKET orders here either — same reason
// as ptSubmitOrder(): the server prices the fill off its own latest tick.
//
// BUGFIX: this used to let any exception from ptDispatchOrder() (or the
// renderPaperTradingPanel() call buried at the end of _ptSendOrderNow())
// propagate straight out. That's fine for a single order, but multi-leg
// callers fire several ptExecuteLeg() calls back-to-back in one JS turn —
// the Decision Engine box's "Execute" button chains them with semicolons
// in one onclick, and ptExecuteStrategy() below chains them in a
// .forEach() — and in both cases an uncaught throw on leg N aborts every
// leg after it with no error surfaced, which looked exactly like "only
// the first leg executed." Wrapping the body here means one bad leg can
// never silently swallow the rest of a batch.
function ptExecuteLeg(symbol, expiry, strike, instrument_type, side, lots, ltp){
  try {
    if(!symbol){ ptToast('No active symbol — cannot execute leg', 'err'); return; }
    // Strategy legs carry their own `ltp` (used to draw the payoff curve) —
    // reuse it as the same MARKET-price guard the panel and quick popover
    // use, rather than sending a leg the backend has nothing to price.
    if((ltp === undefined || ltp === null || isNaN(ltp) || ltp <= 0)){
      ptToast('No live price for this leg — order not sent', 'err');
      return;
    }
    const payload = {
      symbol, instrument_type, expiry, strike, side,
      qty_lots: lots || 1, order_type: 'MARKET', limit_price: null,
    };
    ptDispatchOrder(payload, null);
  } catch(e) {
    Logger.error('paper-trading', 'ptExecuteLeg failed', {symbol, expiry, strike, instrument_type, side, lots, ltp}, e);
    ptToast((side||'') + ' ' + (strike||'') + ' ' + (instrument_type||'') + ' — leg failed to send, see console', 'err');
  }
}
window.ptExecuteLeg = ptExecuteLeg;

function ptExecuteStrategy(){
  if(_ptLiveMode){
    ptToast('Bulk live strategy blocked — confirm and place each leg individually', 'err');
    return;
  }
  const stratSel = document.getElementById('strat-select');
  const strikeSel = document.getElementById('strat-strike-select');
  if(!stratSel || !_data) return;
  const strats = _data.strategies || [];
  const s = strats[parseInt(stratSel.value) || 0];
  if(!s || !(s.legs || []).length) return;

  // Same strike-shift logic renderStratPayoff() uses, so what gets
  // executed matches exactly what the payoff chart / leg pills show.
  const atm = _data.atm || _data.spot || _data.spotPrice || 0;
  const selectedStrike = strikeSel && strikeSel.value ? parseFloat(strikeSel.value) : atm;
  const offset = selectedStrike - (atm || selectedStrike);
  const symbol = _data.symbol || '';
  const expiry = s.expiry || _data.expiry || '';

  const legs = s.legs;
  legs.forEach(l=>{
    const strike = (l.strike || atm) + offset;
    // Prefer the leg's own expiry (calendar spreads) over the blanket
    // strategy-level expiry — see the matching note in renderStratPayoff() —
    // then resolve NEAR/FAR labels to a real date (ptResolveStrategyExpiry)
    // so the order actually carries something the engine can price.
    const legExpiry = ptResolveStrategyExpiry(l.expiry || expiry);
    ptExecuteLeg(symbol, legExpiry, strike, (l.type||'').toUpperCase(), l.action, l.lots || 1, parseFloat(l.ltp));
  });
  ptToast('Executing ' + legs.length + ' leg' + (legs.length===1?'':'s') + ' — ' + (s.name || 'Strategy'), 'ok');
}
window.ptExecuteStrategy = ptExecuteStrategy;

// Decision Engine box's "▶ Execute" button — same idea as ptExecuteStrategy()
// above (re-read the live source of legs at click time, execute each
// through the now fail-safe ptExecuteLeg()), just sourced from
// _data.decision.autoStrategy instead of the Strategy Payoff panel's
// selected strategy. No strike-shift offset here since the decision box
// has no strike picker of its own — legs execute at the strikes shown.
function ptExecuteDecisionStrategy(){
  if(_ptLiveMode){
    ptToast('Bulk live strategy blocked — confirm and place each leg individually', 'err');
    return;
  }
  if(!_data){ ptToast('No live data yet — nothing to execute', 'err'); return; }
  const auto = (_data.decision && _data.decision.autoStrategy) || {};
  const legs = auto.legs || [];
  if(!legs.length){ ptToast('No strategy legs to execute', 'err'); return; }
  const symbol = _data.symbol || '';
  let sent = 0, skipped = 0;
  legs.forEach(l=>{
    const ltp = parseFloat(l.ltp);
    const legLabel = (l.action||'') + ' ' + (l.strike||'') + ' ' + (l.type||'').toUpperCase();
    if(!(ltp > 0)){
      // BUGFIX: this used to `return` here with no toast at all — the pill's
      // own ▶ button is hidden for a leg with no live LTP so there was
      // nothing to click, but the bulk Execute button has no equivalent
      // per-leg cue, so a skipped leg here just looked like nothing
      // happened. Surface it instead: which leg, and the raw ltp value,
      // so a missing/zero price from the decision engine's payload (as
      // opposed to a frontend bug) is visible immediately.
      skipped++;
      Logger.warn('decision-box', 'skipped leg — no live LTP:', l);
      ptToast(legLabel + ' — no live price, not sent', 'err');
      return;
    }
    const legExpiry = ptResolveStrategyExpiry(l.expiry || auto.expiry || _data.expiry || '');
    ptExecuteLeg(symbol, legExpiry, l.strike||0, (l.type||'').toUpperCase(), l.action, l.lots||1, ltp);
    sent++;
  });
  if(sent) ptToast('Executing ' + sent + ' leg' + (sent===1?'':'s') + ' — ' + (auto.name || 'Strategy'), 'ok');
  else ptToast('No legs had a live price — nothing sent', 'err');
}
window.ptExecuteDecisionStrategy = ptExecuteDecisionStrategy;

// BUGFIX: the symbol/expiry/strike/LTP sync below used to live AFTER the
// `if(!wsState.portfolio) return;` guard in the orchestrator, which meant
// NONE of it ever ran until the backend started sending {type:"portfolio",...}
// messages. That message type needs separate wiring into
// ws_server_live.py (see the note above sendWsMessage() in
// paper-trading-shared.js) and may not be hooked up yet — but the option
// chain (which is what actually drives the expiry/strike dropdowns)
// arrives via a completely different, already-working WS message stream.
// So this sync must not be blocked on `portfolio` existing at all — only
// portfolio-tracker.js's P&L/positions/orders rendering genuinely needs
// it. Called directly from portfolio-tracker.js's renderPaperTradingPanel()
// orchestrator, before that guard.
function ptSyncFormFromWsState(wsState){
  // BUGFIX: pt-symbol is prefilled from AppState.wsState.symbol at mount time, but
  // ptMountPanel() runs on DOMContentLoaded — before connectWebSocket()'s
  // first tick — so AppState.wsState.symbol is usually still unknown then and the
  // dropdown silently falls back to whatever's first in PT_LOT_SIZES
  // (NIFTY). Nothing ever re-synced it once the real symbol arrived, so if
  // the backend was actually streaming e.g. BANKNIFTY the form stayed
  // pinned to NIFTY forever and expiry/strike lookups never matched.
  const symSel = $i('pt-symbol');
  const preferredSymbol = !_ptSymbolTouched ? wsState.symbol : (symSel && symSel.value);
  ptPopulateOrderSymbols(wsState.fnoSymbols, preferredSymbol);
  if(symSel && !_ptSymbolTouched && wsState.symbol && symSel.value !== wsState.symbol
     && Array.from(symSel.options).some(o=>o.value===wsState.symbol)){
    symSel.value = wsState.symbol;
    ptRefreshExpiryStrikeOptions();
  }

  // BUGFIX: this used to check `!$i('pt-expiry').options.length`, which is
  // never true once the placeholder option ("Expiry…" / "No data — switch
  // to symbol first") has been added — i.e. always, right after mount. That
  // made the dropdown get stuck showing the placeholder forever whenever
  // the panel mounted before the very first WS tick arrived (the common
  // case on page load), since nothing ever re-triggered the refresh once
  // real chain data showed up. Instead: re-populate whenever the select is
  // still in its "disabled/no data" state AND live data that actually
  // matches the form's current symbol has since become available.
  const expSel = $i('pt-expiry');
  const instypeNow = $i('pt-instype') ? $i('pt-instype').value : '';
  const needsExpiryNow = instypeNow === 'CE' || instypeNow === 'PE' || instypeNow === 'FUT';
  if(expSel && expSel.disabled && needsExpiryNow
     && wsState.symbol === $i('pt-symbol').value
     && ((wsState.chains && Object.keys(wsState.chains).length) || wsState.expiry)){
    ptRefreshExpiryStrikeOptions();
  }
  ptUpdateLtpHint();
}
