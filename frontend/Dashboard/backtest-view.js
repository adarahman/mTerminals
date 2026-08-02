// ============================================================
// backtest-view.js
// Closes the loop on iterating on decision_engine.py's thresholds —
// previously the only way to see backtest/replay.py's output was its own
// `if __name__ == "__main__"` block, printed to a terminal. This is the
// dashboard-side view: a form for the same gating knobs
// decision/auto_executor.py reads live (min confidence, cooldown, daily
// cap, qty lots, optional account-guard simulation) plus an optional
// date range, a "Run backtest" button that calls the server's
// /api/backtest (ws_server_live.py's backtest_handler, wrapping
// backtest/replay.py's run_backtest() against captured decision history
// — see backtest/snapshot_logger.py), and a results view: summary stats,
// a cumulative-P&L equity curve, and the full trade list.
//
// Self-mounted, same pattern as algo-status.js's algoMountPanel() /
// paper-trading.js's ptMountPanel() — one JS file builds its own DOM on
// DOMContentLoaded, no changes needed to DashboardPro.html beyond the
// script/style <link> tags and one nav-rail toggle button. Uses the
// shared .u-modal-overlay (theme.css) rather than algo-panel's small
// slide-out — this view has a form + chart + table, it needs real
// screen space, not a 340px sidebar.
//
// Pure client-side rendering: fetches JSON from /api/backtest and builds
// markup from it. No websocket involvement — a backtest run is an
// explicit, on-demand request, not live tick data.
// ============================================================

function btMountModal(){
  if($i('backtest-modal')) return; // already mounted (e.g. bfcache reload)

  const overlay = document.createElement('div');
  overlay.id = 'backtest-modal';
  overlay.className = 'u-modal-overlay';
  overlay.innerHTML = `
    <div class="bt-panel">
      <div class="bt-bar">
        <span class="bt-title">📈 Backtest — decision engine replay</span>
        <span class="bt-close" onclick="toggleBacktestModal(false)" title="Close">✕</span>
      </div>
      <div class="bt-body">
        <div class="bt-form" id="bt-form"></div>
        <div class="bt-results" id="bt-results">
          <div class="bt-empty">Set your parameters above and run a backtest to see results here.</div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  // Click on the dark backdrop (not the panel itself) closes it — same
  // affordance as every other overlay-based modal.
  overlay.addEventListener('click', (e) => { if(e.target === overlay) toggleBacktestModal(false); });

  btRenderForm();
}

// Rebuilt fresh every time the modal opens (not just once at mount) so
// the symbol field can default to whatever the dashboard's currently
// showing — a user backtesting BANKNIFTY after switching symbols
// shouldn't have to remember to change a stale NIFTY default.
function btRenderForm(){
  const form = $i('bt-form');
  if(!form) return;
  const currentSymbol = (AppState.wsState && AppState.wsState.symbol) || 'NIFTY';
  form.innerHTML = `
    <div class="bt-field">
      <label>Symbol</label>
      <input id="bt-symbol" type="text" value="${ptEscAttr(currentSymbol)}" />
    </div>
    <div class="bt-field">
      <label>Start date</label>
      <input id="bt-start" type="date" />
    </div>
    <div class="bt-field">
      <label>End date</label>
      <input id="bt-end" type="date" />
    </div>
    <div class="bt-field">
      <label>Min confidence</label>
      <input id="bt-min-confidence" type="number" min="0" max="100" value="40" />
    </div>
    <div class="bt-field">
      <label>Cooldown (sec)</label>
      <input id="bt-cooldown" type="number" min="0" value="300" />
    </div>
    <div class="bt-field">
      <label>Max trades/day</label>
      <input id="bt-max-trades" type="number" min="1" value="10" />
    </div>
    <div class="bt-field">
      <label>Qty (lots)</label>
      <input id="bt-qty-lots" type="number" min="1" value="1" />
    </div>
    <div class="bt-field bt-field-checkbox">
      <label><input id="bt-use-guard" type="checkbox" /> Simulate account guard</label>
    </div>
    <div class="bt-field bt-field-run">
      <button id="bt-run-btn" onclick="btRunBacktest()">Run backtest</button>
    </div>
  `;
}

function _btParamsFromForm(){
  const val = id => { const el = $i(id); return el ? el.value : ''; };
  const params = new URLSearchParams();
  params.set('symbol', (val('bt-symbol') || 'NIFTY').trim().toUpperCase());
  if(val('bt-start')) params.set('start', val('bt-start'));
  if(val('bt-end')) params.set('end', val('bt-end'));
  params.set('minConfidence', val('bt-min-confidence') || '40');
  params.set('cooldownSeconds', val('bt-cooldown') || '300');
  params.set('maxTradesPerSymbolPerDay', val('bt-max-trades') || '10');
  params.set('qtyLots', val('bt-qty-lots') || '1');
  params.set('useAccountGuard', $i('bt-use-guard') && $i('bt-use-guard').checked ? 'true' : 'false');
  return params;
}

async function btRunBacktest(){
  const resultsEl = $i('bt-results');
  const runBtn = $i('bt-run-btn');
  if(!resultsEl) return;

  const params = _btParamsFromForm();
  resultsEl.innerHTML = '<div class="bt-loading">Running backtest — replaying decision history through AutoExecutor…</div>';
  if(runBtn){ runBtn.disabled = true; runBtn.textContent = 'Running…'; }

  try{
    const resp = await fetch('/api/backtest?' + params.toString());
    const data = await resp.json();
    if(!resp.ok){
      resultsEl.innerHTML = `<div class="bt-error">⚠ ${ptEscAttr(data.error || ('HTTP ' + resp.status))}</div>`;
      return;
    }
    btRenderResults(data);
  } catch(e){
    resultsEl.innerHTML = `<div class="bt-error">⚠ Request failed: ${ptEscAttr(String(e && e.message || e))}</div>`;
  } finally {
    if(runBtn){ runBtn.disabled = false; runBtn.textContent = 'Run backtest'; }
  }
}
window.btRunBacktest = btRunBacktest;

function _btFmtRupees(n){
  if(n == null || Number.isNaN(n)) return '—';
  const sign = n < 0 ? '−' : '';
  return sign + '₹' + Math.abs(n).toLocaleString('en-IN', {maximumFractionDigits: 0});
}

function btRenderResults(data){
  const resultsEl = $i('bt-results');
  if(!resultsEl) return;

  const s = data.summary || {};
  const pnlCls = (s.total_pnl || 0) < 0 ? 'bt-neg' : 'bt-pos';

  if(!s.num_trades){
    resultsEl.innerHTML = `
      <div class="bt-empty">
        No trades in this range/threshold combination.
        ${s.unpriced_signals ? `${s.unpriced_signals} signal(s) cleared AutoExecutor's gates but had no LTP data to fill against — try a wider date range or check backtest/snapshot_logger.py's captured history for this symbol.` : ''}
      </div>
    `;
    return;
  }

  const summaryHtml = `
    <div class="bt-summary">
      <div class="bt-stat"><span class="bt-stat-label">Trades</span><span class="bt-stat-value">${s.num_trades}</span></div>
      <div class="bt-stat"><span class="bt-stat-label">Total P&amp;L</span><span class="bt-stat-value ${pnlCls}">${_btFmtRupees(s.total_pnl)}</span></div>
      <div class="bt-stat"><span class="bt-stat-label">Win rate</span><span class="bt-stat-value">${s.win_rate != null ? Math.round(s.win_rate * 100) + '%' : '—'}</span></div>
      <div class="bt-stat"><span class="bt-stat-label">Avg P&amp;L / trade</span><span class="bt-stat-value">${_btFmtRupees(s.avg_pnl)}</span></div>
      <div class="bt-stat"><span class="bt-stat-label">Max drawdown</span><span class="bt-stat-value bt-neg">${_btFmtRupees(s.max_drawdown)}</span></div>
      <div class="bt-stat"><span class="bt-stat-label">Unpriced signals</span><span class="bt-stat-value">${s.unpriced_signals ?? 0}</span></div>
    </div>
  `;

  resultsEl.innerHTML = summaryHtml
    + `<div class="bt-section-title">Equity curve</div>`
    + `<div id="bt-equity-curve">${_btBuildEquityCurveSvg(data.equityCurve || [])}</div>`
    + `<div class="bt-section-title">Trades (${(data.trades || []).length})</div>`
    + `<div id="bt-trades-table">${_btBuildTradesTable(data.trades || [])}</div>`;
}

// Deliberately hand-rolled SVG (no chart library dependency, matching
// this codebase's other inline-SVG views) — just a polyline over
// {seq, cumPnl} points, since a backtest's equity curve is exactly that:
// one point per closed trade, in execution order, nothing more.
function _btBuildEquityCurveSvg(points){
  if(!points.length){
    return '<div class="bt-empty">No closed trades to plot.</div>';
  }
  const W = 760, H = 200, PAD = 28;
  const values = points.map(p => p.cumPnl);
  const minV = Math.min(0, ...values);
  const maxV = Math.max(0, ...values);
  const span = (maxV - minV) || 1;

  const xAt = i => points.length === 1
    ? W / 2
    : PAD + (i / (points.length - 1)) * (W - 2 * PAD);
  const yAt = v => H - PAD - ((v - minV) / span) * (H - 2 * PAD);

  const coords = points.map((p, i) => `${xAt(i).toFixed(1)},${yAt(p.cumPnl).toFixed(1)}`);
  const zeroY = yAt(0).toFixed(1);
  const finalCls = values[values.length - 1] < 0 ? 'bt-equity-neg' : 'bt-equity-pos';

  // Area fill under the line, closed back down to the zero line rather
  // than the chart's bottom edge — makes drawdowns below zero visually
  // read as "underwater" instead of just a lower positive fill.
  const areaPoints = `${xAt(0).toFixed(1)},${zeroY} ${coords.join(' ')} ${xAt(points.length - 1).toFixed(1)},${zeroY}`;

  return `
    <svg viewBox="0 0 ${W} ${H}" class="bt-equity-svg ${finalCls}" preserveAspectRatio="none">
      <line x1="${PAD}" y1="${zeroY}" x2="${W - PAD}" y2="${zeroY}" class="bt-equity-zeroline" />
      <polygon points="${areaPoints}" class="bt-equity-area" />
      <polyline points="${coords.join(' ')}" class="bt-equity-line" />
    </svg>
  `;
}

function _btBuildTradesTable(trades){
  if(!trades.length) return '<div class="bt-empty">No trades.</div>';
  const rows = trades.map(t => {
    const closed = t.pnl != null;
    const pnlCls = closed ? (t.pnl < 0 ? 'bt-neg' : 'bt-pos') : '';
    return `
      <tr class="${closed ? '' : 'bt-row-open'}">
        <td>${ptEscAttr(t.side)} ${t.strike} ${ptEscAttr(t.instrumentType)}</td>
        <td>${t.qtyLots}</td>
        <td>${ptEscAttr(t.entryTime || '')}<br><span class="bt-price">@ ${t.entryPrice}</span></td>
        <td>${closed ? ptEscAttr(t.exitTime) + '<br><span class="bt-price">@ ' + t.exitPrice + '</span>' : '<span class="bt-open-tag">OPEN</span>'}</td>
        <td>${ptEscAttr(t.exitReason || '')}</td>
        <td class="${pnlCls}">${closed ? _btFmtRupees(t.pnl) : '—'}</td>
      </tr>
    `;
  }).join('');
  return `
    <table class="bt-table">
      <thead><tr>
        <th>Leg</th><th>Lots</th><th>Entry</th><th>Exit</th><th>Reason</th><th>P&amp;L</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function toggleBacktestModal(open){
  const el = $i('backtest-modal');
  if(!el) return;
  const opening = open !== undefined ? open : !el.classList.contains('open');
  if(opening){
    btRenderForm(); // refresh symbol default each time it's opened
    el.classList.add('open');
  } else {
    el.classList.remove('open');
  }
}
window.toggleBacktestModal = toggleBacktestModal;

window.addEventListener('DOMContentLoaded', btMountModal);
window.addEventListener('keydown', (e) => {
  if(e.key === 'Escape'){
    const el = $i('backtest-modal');
    if(el && el.classList.contains('open')) toggleBacktestModal(false);
  }
});
