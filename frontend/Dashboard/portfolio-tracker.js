// ── portfolio-tracker.js ────────────────────────────────────────────────
// Split 2026-08-05 out of the old monolithic paper-trading.js. Owns
// #pt-portfolio-panel end to end: the P&L/fund summary strip, the open
// positions table (with per-row square-off), and the order/trade log
// table. Also owns the top-level mount orchestrator (ptMountPanel) and
// renderPaperTradingPanel() — the single entry point dashboard.js's WS
// tick handler calls — since both need to reach into order-entry.js
// (ptSyncFormFromWsState) as well as this file's own render functions.
// Depends on paper-trading-shared.js (load first) and order-entry.js
// (load second; this file loads last).

// Builds #pt-portfolio-panel's DOM and wires the one listener that isn't
// a plain inline onclick (the delegated tap-to-reveal for rejected-order
// reasons). Called once by ptMountPanel() below, alongside
// ptMountSharedHosts() (paper-trading-shared.js) and ptMountOrderPanel()
// (order-entry.js).
//
// The toggle button itself is not created here — it's the static
// #pt-toggle-btn "Portfolio" sec-btn in the left #sec-nav-bar rail
// (DashboardPro.html). See order-entry.js's ptMountOrderPanel() comment
// for the fuller history of the 2026-08-04 order/portfolio panel split.
function ptMountPortfolioPanel(){
  const portfolioPanel = document.createElement('div');
  portfolioPanel.id = 'pt-portfolio-panel';
  portfolioPanel.innerHTML = `
    <h4><span>Portfolio Tracker</span> <span id="pt-portfolio-mode-badge" class="pt-mode-toggle paper" title="Order mode — toggle from the Order GUI panel">📝 PAPER</span> <button type="button" class="pt-close" onclick="ptClosePanel('pt-portfolio-panel','pt-toggle-btn')" aria-label="Close portfolio panel">✕</button></h4>
    <div class="pt-section" style="font-size:10px;line-height:1.45;color:var(--text-muted,#888);">
      <strong style="color:var(--text-primary,#eee);">SIMULATION ONLY</strong> — fills are not exchange confirmations. MARKET fills use the backend's latest live tick; LIMIT fills use the live tick that crosses the limit. No added slippage or artificial delay; quantity is lots × resolved lot size.
    </div>
    <div class="pt-section">
      <div class="pt-summary"><span>Realized</span><span id="pt-realized">—</span></div>
      <div class="pt-summary"><span>Unrealized</span><span id="pt-unrealized">—</span></div>
      <div class="pt-summary"><span>Total P&amp;L (gross)</span><span id="pt-total">—</span></div>
      <div class="pt-summary" style="opacity:.85;">
        <span title="Charges on FILLED orders since the last Reset (matches the trade log below) — click Reset to zero this out along with the log. Total P&amp;L above is the backend's real portfolio state and always reflects full history regardless of Reset.">Charges (since reset, <span id="pt-charges-count">0</span> orders)</span><span id="pt-charges">—</span>
      </div>
      <div class="pt-summary" style="font-weight:800;border-top:1px solid var(--border,#333);padding-top:4px;margin-top:2px;">
        <span>Net P&amp;L (after charges)</span><span id="pt-net-pnl">—</span>
      </div>
      <div class="pt-summary" style="opacity:.7;font-size:11px;">
        <span title="Estimated — assumes exit fills at current LTP; a real MARKET order can slip.">If squared off now (est.)</span><span id="pt-net-pnl-if-flat">—</span>
      </div>
      <div class="pt-summary" style="opacity:.7;font-size:11px;">
        <span title="Approximate — long options at premium paid, short/written options at PT_SHORT_MARGIN_PCT of notional (no real SPAN+exposure calc available client-side).">Margin used (approx.)</span><span id="pt-margin-used">—</span>
      </div>
      <div class="pt-summary" style="font-weight:800;border-top:1px solid var(--border,#333);padding-top:4px;margin-top:2px;">
        <span title="Backend paper equity (₹1,00,000 starting capital plus gross realized/unrealized P&amp;L) minus approximate open-position margin. Simulated charges are shown separately and are not deducted from this gross fund figure.">Fund (available, gross)</span><span id="pt-fund">—</span>
      </div>
      <div id="pt-fund-warn" style="display:none;font-size:10px;color:var(--neg,#e74c3c);margin-top:4px;">⚠ Fund running low — consider squaring off open positions.</div>
    </div>
    <div class="pt-section">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
        <span style="font-size:10px;font-weight:700;color:var(--text-muted,#888);text-transform:uppercase;letter-spacing:.05em;">Positions</span>
        <button id="pt-squareoff-all-btn" onclick="ptSquareOffAll()" title="Send an opposite-side MARKET order to flatten every open position"
          style="font-size:10px;font-weight:700;padding:3px 8px;border:none;border-radius:4px;background:var(--neg,#e74c3c);color:#fff;cursor:pointer;">Square Off All</button>
      </div>
      <div class="pt-table-scroll">
      <table id="pt-positions-table" class="pt-table"><thead><tr>
        <th>Sym</th><th>Expiry</th><th>Strike/Ty</th><th>Net</th><th>Avg</th><th>LTP</th><th>uPnL</th><th></th>
      </tr></thead><tbody></tbody></table>
      </div>
    </div>
    <div class="pt-section">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
        <span style="font-size:10px;font-weight:700;color:var(--text-muted,#888);text-transform:uppercase;letter-spacing:.05em;">Order / Trade Log</span>
        <button id="pt-reset-log-btn" onclick="ptResetOrderLog()" title="Clear the order/trade log shown below (does not affect open positions or P&amp;L)"
          style="font-size:10px;font-weight:700;padding:3px 8px;border:none;border-radius:4px;background:var(--bg-2,#555);color:var(--text-primary,#fff);border:1px solid var(--border,#333);cursor:pointer;">Reset</button>
      </div>
      <div class="pt-table-scroll">
      <table id="pt-orders-table" class="pt-table"><thead><tr>
        <th>Sym</th><th>Expiry</th><th>Side</th><th>Qty</th><th>Type</th><th>Price</th><th>Charges</th><th>Status</th><th>Time</th>
      </tr></thead><tbody></tbody></table>
      </div>
    </div>
  `;
  document.body.appendChild(portfolioPanel);

  // Tap-to-reveal for rejected orders' reason (see .pt-status-tap CSS note
  // in styles/paper-trading.css) — delegated once on the table body since
  // rows are rebuilt on every re-render. Lives here — that's where the
  // orders table (#pt-orders-table) actually is.
  portfolioPanel.addEventListener('click', (e)=>{
    const td = e.target.closest('.pt-status-tap');
    if(td) ptToast('Rejected — ' + (td.dataset.reason || 'no reason provided'), 'err');
  });
}

function togglePortfolioPanel(){
  ptTogglePanelNear('pt-portfolio-panel', 'pt-toggle-btn');
}
window.togglePortfolioPanel = togglePortfolioPanel;

// Flattens one open position with a single opposite-side MARKET order —
// e.g. net_qty_lots=+3 (long) sends a SELL 3, net_qty_lots=-2 (short)
// sends a BUY 2. Routes through ptDispatchOrder() (paper-trading-shared.js)
// like every other order path, so it shows up as a normal FILLED order in
// the orders table and the position disappears from the positions table
// once the next portfolio broadcast lands (place_order -> _apply_fill_to_position
// nets it to zero, and get_positions() only returns net_qty_lots != 0 rows).
function ptSquareOffPosition(symbol, expiry, strike, instrument_type, net_qty_lots){
  const qty = Math.abs(net_qty_lots);
  if(!qty) return;
  const side = net_qty_lots > 0 ? 'SELL' : 'BUY';
  const payload = {
    symbol, instrument_type, expiry, strike, side,
    qty_lots: qty, order_type: 'MARKET', limit_price: null,
  };
  ptDispatchOrder(payload, null);
}
window.ptSquareOffPosition = ptSquareOffPosition;

function ptSquareOffAll(){
  const positions = (AppState.wsState && AppState.wsState.portfolio && AppState.wsState.portfolio.positions) || [];
  const open = positions.filter(p => p.net_qty_lots);
  if(!open.length) return;
  open.forEach(p => ptSquareOffPosition(p.symbol, p.expiry, p.strike, p.instrument_type, p.net_qty_lots));
  ptToast('Squaring off ' + open.length + ' position' + (open.length===1?'':'s'), 'ok');
}
window.ptSquareOffAll = ptSquareOffAll;

function ptCancelOrder(orderId){
  if(!orderId) return;
  if(!confirm('Cancel this pending order?')) return;
  sendWsMessage('cancel_order', { order_id: orderId });
}
window.ptCancelOrder = ptCancelOrder;

// Rejected orders were previously silent: they'd just show up in the log
// with status "REJECTED" and no explanation, so a rejected MARKET order
// (e.g. because the backend had no price to fill it at) looked identical
// to any other row unless you were staring at the table when it changed.
// Toast the first time each rejected order is seen, including whatever
// reason field the backend attaches, so the cause is visible immediately.
const _ptSeenOrderKeys = new Set();
function ptNotifyNewRejections(orders){
  orders.forEach(o=>{
    const key = o.id || o.order_id || [o.symbol,o.instrument_type,o.strike,o.side,o.qty_lots,o.timestamp].join('|');
    if(_ptSeenOrderKeys.has(key)) return;
    _ptSeenOrderKeys.add(key);
    if(String(o.status||'').toUpperCase() === 'REJECTED'){
      const reason = o.reason || o.reject_reason || o.rejection_reason || o.message || o.error
        || 'no price available for this instrument at fill time';
      const label = o.symbol + (o.strike ? ' ' + o.strike + ' ' + o.instrument_type : ' ' + (o.instrument_type||''));
      ptToast(label + ' — REJECTED: ' + reason, 'err');
    }
  });
}

// ── Pure calculation: wsState -> portfolio view-model ──────────────────
// No DOM access anywhere in this function. Everything renderPaperTrading-
// Panel's three render functions need — repriced positions, charges, net
// P&L, fund summary, the filtered order log — is computed once here and
// handed to them as plain data, so the P&L math can be read, tested, or
// reused (e.g. by a future export/summary feature) independently of how
// it happens to be painted to the DOM today.
function ptComputePortfolioView(wsState){
  const pf = ptLiveReprice(wsState.portfolio, wsState);

  // Realized/Unrealized/Total above are gross mark-to-market — the actual
  // amount you'd walk away with is that minus statutory charges incurred
  // on every FILLED leg (STT, exchange transaction charge, SEBI fee,
  // stamp duty, GST — see ptCalcCharges). Shown as its own line rather
  // than baked silently into "Total P&L" so gross vs. net stays visible.
  //
  // Charges/Net P&L are computed client-side (unlike Realized/Unrealized/
  // Total P&L, which are the backend's actual portfolio truth and can't
  // be reset without actually squaring off positions) — so "Reset" clears
  // them the same way it clears the visible order log: orders before
  // _ptOrdersResetAt are excluded. That means after a Reset, Net P&L is
  // only a true reconciliation of Total P&L for trades placed since the
  // reset — pre-reset charges still happened and Total P&L above still
  // reflects them, it's just this line that's been zeroed out for a
  // fresh start.
  const ordersSinceReset = (wsState.orders || []).filter(o=>{
    const tsVal = o.fill_timestamp ?? o.timestamp;
    return !tsVal || tsVal >= _ptOrdersResetAt;
  });
  const totalCharges = ptTotalCharges(ordersSinceReset);
  const filledCount = ordersSinceReset.filter(o=>String(o.status||'').toUpperCase()==='FILLED').length;
  const netPnl = pf.total_pnl - totalCharges;

  // Forward-looking: what you'd actually walk away with if every open
  // position were flattened right now, including the exit-leg charges
  // that haven't been incurred yet (see ptEstimateExitCharges).
  const estExitCharges = ptEstimateExitCharges(pf.positions || []);
  const netPnlIfFlat = netPnl - estExitCharges;

  // Fund / available margin — see ptComputeFundSummary() (paper-trading-shared.js)
  // for the capital/margin model. Uses the same wsState so this stays in
  // lockstep with Realized/Unrealized/Total rather than recomputing pf a
  // second time.
  const fundSummary = ptComputeFundSummary(wsState);

  return { pf, ordersSinceReset, totalCharges, filledCount, netPnl, estExitCharges, netPnlIfFlat, fundSummary };
}

// ── Render: P&L summary strip (realized/unrealized/total/charges/net/fund) ──
function ptRenderPortfolioSummary(view){
  const { pf, totalCharges, filledCount, netPnl, netPnlIfFlat, fundSummary } = view;
  setHtmlIfChanged($i('pt-realized'), '<span class="'+ptPnlClass(pf.realized_pnl)+'">'+ptFmtN(pf.realized_pnl)+'</span>');
  setHtmlIfChanged($i('pt-unrealized'), '<span class="'+ptPnlClass(pf.unrealized_pnl)+'">'+ptFmtN(pf.unrealized_pnl)+'</span>');
  setHtmlIfChanged($i('pt-total'), '<span class="'+ptPnlClass(pf.total_pnl)+'">'+ptFmtN(pf.total_pnl)+'</span>');
  setHtmlIfChanged($i('pt-charges'), '<span class="pt-neg">−'+ptFmtN(totalCharges)+'</span>');
  setHtmlIfChanged($i('pt-charges-count'), String(filledCount));
  setHtmlIfChanged($i('pt-net-pnl'), '<span class="'+ptPnlClass(netPnl)+'">'+ptFmtN(netPnl)+'</span>');
  setHtmlIfChanged($i('pt-net-pnl-if-flat'), '<span class="'+ptPnlClass(netPnlIfFlat)+'">'+ptFmtN(netPnlIfFlat)+'</span>');

  if (fundSummary) {
    if (fundSummary.fundSource === 'live-unavailable') {
      // No real AngelOne funds fetch exists yet (see ptComputeFundSummary's
      // comment in paper-trading-shared.js) — say so plainly rather than
      // showing a paper number that would look like a real balance.
      setHtmlIfChanged($i('pt-margin-used'), '<span title="Live account margin isn\'t wired up yet — see paper-trading-shared.js">not available (live)</span>');
      setHtmlIfChanged($i('pt-fund'), '<span title="Live account funds aren\'t wired up yet — see paper-trading-shared.js">not available (live)</span>');
    } else if (fundSummary.fundSource === 'live-real') {
      setHtmlIfChanged($i('pt-margin-used'), '<span title="From AngelOne rmsLimit()">'+ptFmtN(fundSummary.marginBlocked)+'</span>');
      setHtmlIfChanged($i('pt-fund'), '<span title="From AngelOne rmsLimit() — real account funds">'+ptFmtN(fundSummary.fund)+'</span>');
    } else {
      setHtmlIfChanged($i('pt-margin-used'), ptFmtN(fundSummary.marginBlocked));
      setHtmlIfChanged($i('pt-fund'), '<span class="'+(fundSummary.lowFund?'pt-neg':'')+'">'+ptFmtN(fundSummary.fund)+'</span>');
    }
    const warnEl = $i('pt-fund-warn');
    if (warnEl) warnEl.style.display = fundSummary.lowFund ? 'block' : 'none';
  }
}

// ── Render: open positions table ──
function ptRenderPositionsTable(view){
  const { pf } = view;
  const posRows = (pf.positions || []).map(p=>{
    const label = (p.instrument_type === 'CE' || p.instrument_type === 'PE')
      ? p.strike + ' ' + p.instrument_type : p.instrument_type;
    const hasExpiry = p.instrument_type === 'CE' || p.instrument_type === 'PE' || p.instrument_type === 'FUT';
    const expCell = hasExpiry ? ptFmtExpiry(p.expiry) : '—';
    const exitBtn = '<button type="button" onclick="ptSquareOffPosition(\''+p.symbol+'\',\''+(p.expiry||'')+'\','
      + (p.strike==null?'null':p.strike) + ',\''+p.instrument_type+'\','+p.net_qty_lots+')" '
      + 'title="Exit this position (opposite-side MARKET order)" '
      + 'style="cursor:pointer;font-size:9px;font-weight:800;padding:1px 6px;border-radius:4px;'
      + 'background:var(--neg,#e74c3c);color:#fff;border:0;" aria-label="Exit this position">✕</button>';
    return '<tr><td>'+p.symbol+'</td><td title="'+(p.expiry||'')+'">'+expCell+'</td><td>'+label+'</td><td>'+p.net_qty_lots+'</td>'
      + '<td>'+ptFmtN(p.avg_price)+'</td><td>'+ptFmtN(p.last_price)+(p._live?' <span title="live" style="color:var(--pos,#2ecc71);">●</span>':'')+'</td>'
      + '<td class="'+ptPnlClass(p.unrealized_pnl)+'">'+ptFmtN(p.unrealized_pnl)+'</td>'
      + '<td>'+exitBtn+'</td></tr>';
  }).join('') || '<tr><td colspan="8" style="text-align:center;opacity:.5">No open positions</td></tr>';
  setHtmlIfChanged($i('pt-positions-table').querySelector('tbody'), posRows);
  const squareOffBtn = $i('pt-squareoff-all-btn');
  if(squareOffBtn){
    const hasPositions = (pf.positions || []).length > 0;
    squareOffBtn.disabled = !hasPositions;
    squareOffBtn.style.opacity = hasPositions ? '1' : '.4';
    squareOffBtn.style.cursor = hasPositions ? 'pointer' : 'default';
  }
}

// ── Render: order/trade log table (confirmed + still-pending rows) ──
function ptRenderOrdersTable(view, wsState){
  // Real backend-confirmed orders first, then any not-yet-confirmed
  // orders sent from this tab, so something always shows up the instant
  // "Place Order"/BUY/SELL is clicked instead of an empty table until
  // the next `orders` WS message arrives.
  // Orders/pending older than _ptOrdersResetAt are filtered out by the
  // "Reset" button (ptResetOrderLog) — same ordersSinceReset computed by
  // ptComputePortfolioView, reused here so the visible log and the
  // charges total can never drift out of sync with each other.
  const orders = view.ordersSinceReset;
  ptNotifyNewRejections(wsState.orders || []);
  const rowsAll = orders.slice(0, 15).map(o=>{
    const hasExpiry = o.instrument_type==='CE' || o.instrument_type==='PE' || o.instrument_type==='FUT';
    const label = (o.instrument_type==='CE'||o.instrument_type==='PE') ? (o.strike ? o.strike+' '+o.instrument_type : o.instrument_type) : (o.instrument_type||'');
    const priceVal = o.fill_price ?? o.limit_price;
    const tsVal = o.fill_timestamp ?? o.timestamp;
    const symText = o.symbol+(label?' '+label:'');
    const expCell = hasExpiry ? ptFmtExpiry(o.expiry) : '—';
    const sideCls = o.side === 'BUY' ? 'pt-side-buy' : 'pt-side-sell';
    const statusReason = o.reason || o.reject_reason || o.rejection_reason || o.message || o.error || '';
    const isRejected = String(o.status||'').toUpperCase()==='REJECTED';
    const isFilled = String(o.status||'').toUpperCase()==='FILLED';
    const isPending = String(o.status||'').toUpperCase()==='PENDING';

    const displayStatus = isFilled ? 'SIM FILLED' : o.status;
    let statusTd = '<td title="Paper lifecycle: Submitted → '+displayStatus+' → Position/Closed">'+displayStatus+'</td>';
    if(isRejected){
      statusTd = '<td class="pt-neg pt-status-tap" data-reason="'+ptEscAttr(statusReason || 'No reason provided by engine')+'" title="Paper simulation rejected — tap for reason">SIM REJECTED</td>';
    } else if(isPending){
      const cancelBtn = '<button type="button" onclick="ptCancelOrder(\''+o.id+'\')" title="Cancel this pending order" '
        + 'style="cursor:pointer;margin-left:6px;font-size:9px;font-weight:800;padding:1px 4px;border-radius:3px;'
        + 'background:var(--neg,#e74c3c);color:#fff;border:0;" aria-label="Cancel this pending order">✕</button>';
      statusTd = '<td>'+o.status + cancelBtn + '</td>';
    }

    // Only a FILLED order actually incurs statutory charges — a REJECTED
    // or still-PENDING order never executed, so there's no turnover to
    // charge against.
    let chargesTd = '<td style="opacity:.4;">—</td>';
    if(isFilled){
      const lot = ptGetLotSize(o.symbol);
      if(lot == null){
        ptWarnUnresolvedLot(o.symbol);
        chargesTd = '<td style="opacity:.4;" title="Lot size not resolved yet">…</td>';
      } else {
        const c = ptCalcCharges(priceVal, o.qty_lots, lot, o.side);
        chargesTd = '<td class="pt-neg" title="STT '+ptFmtN(c.stt,2)+' · Exch '+ptFmtN(c.exchangeTxn,2)
          +' · SEBI '+ptFmtN(c.sebiFee,2)+' · Stamp '+ptFmtN(c.stampDuty,2)+' · GST '+ptFmtN(c.gst,2)+'">−'
          +ptFmtN(c.total,2)+'</td>';
      }
    }
    return '<tr><td title="'+symText+'">'+symText+'</td><td title="'+(o.expiry||'')+'">'+expCell+'</td>'
      + '<td><span class="pt-side-badge '+sideCls+'">'+o.side+'</span></td><td>'+o.qty_lots+'</td>'
      + '<td>'+o.order_type+'</td><td>'+ptFmtN(priceVal, 2)+'</td>' + chargesTd
      + statusTd + '<td>'+(tsVal ? new Date(tsVal*1000).toLocaleString('en-IN', {
  day: '2-digit', month: 'short', year: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
  hour12: true
}) : '—')+'</td></tr>';
  });
  const rowsPending = _ptPending
    .filter(p=>p.ts >= _ptOrdersResetAt*1000)
    // A pending row whose matching confirmed order has already arrived
    // (FILLED or REJECTED, now visible in rowsAll above) doesn't need to
    // keep showing separately — this used to only get cleaned up at the
    // 10s mark, so a normal fast fill briefly showed as two rows.
    .filter(p=>!ptFindMatchingConfirmedOrder(p, orders))
    .map(p=>{
    const hasExpiry = p.instrument_type==='CE' || p.instrument_type==='PE' || p.instrument_type==='FUT';
    const label = (p.instrument_type==='CE'||p.instrument_type==='PE') ? (p.strike ? p.strike+' '+p.instrument_type : p.instrument_type) : (p.instrument_type||'');
    const symText = p.symbol+(label?' '+label:'');
    const expCell = hasExpiry ? ptFmtExpiry(p.expiry) : '—';
    const sideCls = p.side === 'BUY' ? 'pt-side-buy' : 'pt-side-sell';
    const isTimeout = p.status === 'TIMEOUT';
    const statusTd = isTimeout
      ? '<td class="pt-neg pt-status-tap" data-reason="'+ptEscAttr('No confirmation received from the server for this order — it may not have been processed (e.g. an order type the backend doesn\'t support yet), or the response was lost. It was NOT necessarily filled; check Positions before assuming otherwise.')+'" title="Tap for details">No response</td>'
      : '<td>'+p.status+'…</td>';
    return '<tr style="opacity:'+(isTimeout?'1':'.7')+';"><td title="'+symText+'">'+symText+'</td><td title="'+(p.expiry||'')+'">'+expCell+'</td>'
      + '<td><span class="pt-side-badge '+sideCls+'">'+p.side+'</span></td><td>'+p.qty_lots+'</td>'
      + '<td>'+p.order_type+'</td><td>'+ptFmtN(p.limit_price, 2)+'</td><td style="opacity:.4;">—</td>'
      + statusTd + '<td>'+new Date(p.ts).toLocaleTimeString()+'</td></tr>';
  });
  const ordRows = (rowsPending.join('') + rowsAll.join(''))
    || '<tr><td colspan="9" style="text-align:center;opacity:.5">No orders yet</td></tr>';
  setHtmlIfChanged($i('pt-orders-table').querySelector('tbody'), ordRows);
}

// ── Mount orchestrator ── guards on already-mounted, then wires up the
// three pieces in order: shared DOM hosts (toast/quick-popover/live-confirm
// overlay — paper-trading-shared.js), the order panel (order-entry.js),
// and finally this file's own portfolio panel.
function ptMountPanel(){
  if($i('pt-order-panel')) return; // already mounted, e.g. after bfcache-forced reload path
  ptMountSharedHosts();
  ptMountOrderPanel();
  ptMountPortfolioPanel();
}

// ── Orchestrator ── unchanged entry point / call signature, now just
// wires: mount -> form sync (order-entry.js, always) -> guard -> compute
// -> render x3 (this file).
function renderPaperTradingPanel(wsState){
  if(!$i('pt-order-panel')) ptMountPanel();
  if(!wsState) return;

  // Must not be blocked on `portfolio` existing — see order-entry.js's
  // ptSyncFormFromWsState comment.
  ptSyncFormFromWsState(wsState);

  // Everything from here on (P&L summary, positions table, orders table)
  // genuinely does need the backend's paper-trading portfolio feed, so
  // this is the right place — and the ONLY place — to bail on it missing.
  if(!wsState.portfolio) return;

  const view = ptComputePortfolioView(wsState);
  ptRenderPortfolioSummary(view);
  ptRenderPositionsTable(view);
  ptRenderOrdersTable(view, wsState);
}

// Clears what the Order/Trade Log table displays. This is a display-only
// reset (filters rows by timestamp), not a backend wipe — realized P&L
// and position history are computed server-side and are untouched, which
// is the correct behavior: "reset the log" should not silently rewrite
// the actual trading record. Persisted in localStorage so the cleared
// view survives a page reload.
let _ptOrdersResetAt = parseFloat(localStorage.getItem('pt_orders_reset_at') || '0') || 0;
function ptResetOrderLog(){
  if(!confirm('Clear the Order/Trade log, Charges, and Net P&L shown here? Positions and Total P&L (gross) are backend state and are not affected — only these display figures reset.')) return;
  _ptOrdersResetAt = Date.now() / 1000;
  try{ localStorage.setItem('pt_orders_reset_at', String(_ptOrdersResetAt)); }catch(e){}
  _ptPending = [];
  ptToast('Order/Trade log, Charges & Net P&L cleared', 'ok');
  if(AppState.wsState) renderPaperTradingPanel(AppState.wsState);
}
window.ptResetOrderLog = ptResetOrderLog;
window.renderPaperTradingPanel = renderPaperTradingPanel;


window.addEventListener('DOMContentLoaded', ptMountPanel);
