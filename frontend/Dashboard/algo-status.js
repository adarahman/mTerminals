// ============================================================
// algo-status.js
// Surfaces the backend's algo-trading safety stack — LIVE_TRADING_ENABLED,
// the shared kill-switch file, risk/account_guard.py's daily-loss/exposure/
// drawdown state, and decision/auto_executor.py's gating — none of which
// had any UI presence before this file. Without it, the only way to know
// the account guard tripped (or that auto-execution rejected every tick
// for some reason) was to tail server logs.
//
// Purely a read/display panel: it sends nothing to the server and cannot
// itself arm or disarm anything (the kill switch is a file on disk by
// design — see ws_server_live.py's own comment on why that's deliberate).
//
// Fed by {"type":"algoStatus","payload":{...}} — ws_server_live.py's
// algo_status_loop() broadcasts this on its own timer (default 5s) plus
// once on initial connection. MarketStore.ingest()'s generic branch lands
// it at wsState.algoStatus for free (see market-store.js), same mechanism
// indexQuotes/funds already use — no changes needed there.
//
// Self-mounts on DOMContentLoaded, same pattern as paper-trading.js's
// ptMountPanel(). The toggle button itself is no longer created here —
// it's the static #algo-toggle-btn "Algo" sec-btn in the left
// #sec-nav-bar rail (DashboardPro.html), relocated (2026-08-02) from a
// floating fixed bottom-left button that sat permanently on top of
// whatever table row happened to be scrolled underneath it. This
// function now only builds the slide-out panel + wires toggleAlgoPanel(),
// which opens it positioned next to that rail button (same approach as
// ui-controls.js's toggleControlSidebar()) instead of a hardcoded corner.
// Styling in styles/algo-status.css.
// ============================================================

function algoMountPanel(){
  if($i('algo-panel')) return; // already mounted (e.g. bfcache reload)

  const panel = document.createElement('div');
  panel.id = 'algo-panel';
  panel.innerHTML = `
    <h4><span>Algo Status</span> <span class="algo-close" onclick="$i('algo-panel').classList.remove('open')">✕</span></h4>
    <div id="algo-panel-body">Waiting for first status update…</div>
  `;
  document.body.appendChild(panel);
}

// Mirrors ui-controls.js's toggleControlSidebar(): the panel isn't docked
// to a fixed corner anymore, so position it next to wherever the rail
// button actually is (its position in #sec-nav-bar can shift slightly
// depending on which sec-btn-* items are currently visible), clamped so
// it can never render partially off-screen.
function toggleAlgoPanel(){
  const el = $i('algo-panel');
  if(!el) return;
  const opening = !el.classList.contains('open');
  el.classList.toggle('open');
  if(opening){
    const btn = $i('algo-toggle-btn');
    if(btn){
      const r = btn.getBoundingClientRect();
      const vw = window.innerWidth, vh = window.innerHeight;
      const EDGE_MARGIN = 16;
      let left = Math.max(r.right + 8, EDGE_MARGIN);
      let top  = r.top;
      requestAnimationFrame(()=>{
        const pw = el.offsetWidth || 340;
        const ph = el.offsetHeight || 200;
        if(left + pw > vw - 8) left = Math.max(EDGE_MARGIN, vw - pw - 8);
        if(top + ph > vh - 8) top = Math.max(EDGE_MARGIN, vh - ph - 8);
        el.style.left = left + 'px';
        el.style.top  = top + 'px';
      });
      el.style.left = left + 'px';
      el.style.top  = top + 'px';
    }
  }
}
window.toggleAlgoPanel = toggleAlgoPanel;

// Small helper: colored ON/OFF-style badge, reused for every boolean row
// below instead of hand-rolling the same span markup four times.
function _algoBadge(isGood, goodText, badText){
  const cls = isGood ? 'algo-badge-ok' : 'algo-badge-bad';
  return `<span class="algo-badge ${cls}">${isGood ? goodText : badText}</span>`;
}

function _algoFmtRupees(n){
  if(n == null || Number.isNaN(n)) return '—';
  const sign = n < 0 ? '-' : '';
  return sign + '₹' + Math.abs(n).toLocaleString('en-IN', {maximumFractionDigits: 0});
}

function _algoFmtAgo(tsSeconds){
  if(tsSeconds == null) return 'never';
  const secs = Math.max(0, (Date.now()/1000) - tsSeconds);
  if(secs < 60) return Math.floor(secs) + 's ago';
  if(secs < 3600) return Math.floor(secs/60) + 'm ago';
  return Math.floor(secs/3600) + 'h ago';
}

// Renders decision/auto_executor.py's history feed — status.autoExecutor
// .history, newest-first entries of shape {ts, symbol, side,
// instrument_type, strike, qty_lots, status, reason} (see
// AutoExecutor._record_history's docstring for exactly what qualifies:
// only actual submit attempts, 'executed' or 'rejected' — routine misses
// like WAIT/low-confidence/cooldown never reach this list, those are
// already covered by execDetail's "Last execution"/last-decision line
// above). This is the primary trust/distrust surface for auto-execution:
// distinct from the manual paper/live orders table (paper-trading.js),
// specifically "what did the algo itself attempt, and did it go through".
function buildAutoTradeHistoryHtml(history){
  if(!history || !history.length){
    return '<div class="algo-history-empty">No auto-executed trades yet.</div>';
  }
  return history.map(h => {
    const executed = String(h.status).toLowerCase() === 'executed';
    const statusCls = executed ? 'algo-history-executed' : 'algo-history-rejected';
    const statusLabel = executed ? 'EXECUTED' : 'REJECTED';
    const legLabel = [h.side, h.strike, h.instrument_type].filter(Boolean).join(' ')
      || h.symbol || '—';
    const qty = h.qty_lots != null ? `${h.qty_lots} lot${h.qty_lots===1?'':'s'}` : '';
    return `
      <div class="algo-history-item ${statusCls}">
        <div class="algo-history-top">
          <span class="algo-history-leg">${ptEscAttr(legLabel)}${qty ? ' · ' + qty : ''}</span>
          <span class="algo-history-badge">${statusLabel}</span>
        </div>
        <div class="algo-history-meta">
          <span class="algo-history-symbol">${ptEscAttr(h.symbol || '')}</span>
          <span class="algo-history-time">${_algoFmtAgo(h.ts)}</span>
        </div>
        <div class="algo-history-reason">${ptEscAttr(h.reason || '')}</div>
      </div>
    `;
  }).join('');
}

function buildAlgoStatusHtml(status){
  if(!status) return '<div class="algo-empty">No status received yet.</div>';

  const liveOn = !!status.liveTradingEnabled;
  const killActive = !!status.killSwitchActive;
  const guard = status.accountGuard || {};
  const exec = status.autoExecutor || {};
  const guardTripped = !!guard.tripped;
  const execEnabled = !!exec.enabled;

  // Overall banner: kill switch / guard trip is the single most important
  // thing to see at a glance, so it gets its own line above everything
  // else whenever either is active — same "loudest signal first" posture
  // account_guard.py itself uses (log everything, but only surface the
  // expensive signal prominently).
  let banner = '';
  if(killActive || guardTripped){
    const reason = guard.trip_reason ? ` — ${guard.trip_reason}` : '';
    banner = `<div class="algo-alert">⛔ Live trading kill switch is ACTIVE${reason}</div>`;
  }

  const rows = [
    ['Live trading', ''], // overwritten below with dedicated live/paper styling
    ['Kill switch', _algoBadge(!killActive, 'clear', 'ACTIVE')],
    ['Auto-execution', _algoBadge(!execEnabled, 'off', 'ON')],
    ['Account guard', _algoBadge(!guardTripped, 'OK', 'TRIPPED')],
  ];

  // liveOn=true is not itself "bad" (that's the point of the feature), so
  // give it its own neutral styling instead of reusing the danger/ok badge
  // semantics the other three rows use.
  rows[0][1] = liveOn
    ? '<span class="algo-badge algo-badge-live">🔴 LIVE</span>'
    : '<span class="algo-badge algo-badge-ok">paper only</span>';

  const rowsHtml = rows.map(([label, badge]) =>
    `<div class="algo-row"><span class="algo-label">${label}</span>${badge}</div>`
  ).join('');

  const dailyPnl = guard.daily_pnl;
  const pnlCls = dailyPnl != null && dailyPnl < 0 ? 'algo-neg' : 'algo-pos';
  const openLots = guard.current_open_lots;
  const maxLots = guard.max_open_lots;
  const lotsCls = (openLots != null && maxLots != null && openLots >= maxLots) ? 'algo-neg' : 'algo-pos';
  const guardDetail = `
    <div class="algo-section-title">Account guard</div>
    <div class="algo-row"><span class="algo-label">Daily P&amp;L</span>
      <span class="${pnlCls}">${_algoFmtRupees(dailyPnl)}</span>
      <span class="algo-limit">limit ${_algoFmtRupees(-(guard.daily_loss_limit_rupees ?? 0))}</span>
    </div>
    <div class="algo-row"><span class="algo-label">Drawdown streak</span>
      <span>${guard.consecutive_drawdowns ?? '—'}</span>
      <span class="algo-limit">limit ${guard.max_consecutive_drawdowns ?? '—'}</span>
    </div>
    <div class="algo-row"><span class="algo-label">Open lots</span>
      <span class="${lotsCls}">${openLots ?? '—'}</span>
      <span class="algo-limit">limit ${maxLots ?? '—'}</span>
    </div>
  `;

  const execDetail = `
    <div class="algo-section-title">Auto-executor (${status.symbol || '—'})</div>
    <div class="algo-row"><span class="algo-label">Trades today</span>
      <span>${exec.trades_today ?? 0}</span>
      <span class="algo-limit">cap ${exec.max_trades_per_symbol_per_day ?? '—'}/day</span>
    </div>
    <div class="algo-row"><span class="algo-label">Confidence floor</span>
      <span class="algo-limit">${exec.min_confidence ?? '—'}</span>
    </div>
    <div class="algo-row"><span class="algo-label">Cooldown</span>
      <span class="algo-limit">${exec.cooldown_seconds ?? '—'}s</span>
    </div>
    <div class="algo-row"><span class="algo-label">Last execution</span>
      <span class="algo-limit">${_algoFmtAgo(exec.last_execution_ts)}</span>
    </div>
    <div class="algo-last-decision ${exec.last_decision_should_execute ? 'algo-pos' : ''}">
      ${exec.last_decision_reason ? '“' + ptEscAttr(exec.last_decision_reason) + '”' : 'No decision evaluated yet.'}
    </div>
  `;

  // Distinct from the manual paper/live orders table (paper-trading.js) —
  // this is specifically what AutoExecutor actually attempted to act on
  // (evaluate() cleared, i.e. status.autoExecutor.history — see
  // auto_executor.py's _record_history docstring for why routine misses
  // like WAIT/low-confidence/cooldown are NOT in this list; those are
  // already covered by "Last execution"/execDetail above). Newest first,
  // server already trims to the most recent 30 for the broadcast.
  const historyDetail = `
    <div class="algo-section-title">Auto-trade feed</div>
    <div class="algo-history-list">${buildAutoTradeHistoryHtml(exec.history)}</div>
  `;

  const caps = `
    <div class="algo-section-title">Per-order caps</div>
    <div class="algo-row"><span class="algo-label">Max lots/order</span>
      <span class="algo-limit">${status.maxLotsPerOrder ?? '—'}</span>
    </div>
    <div class="algo-row"><span class="algo-label">Max orders/min</span>
      <span class="algo-limit">${status.maxOrdersPerMinute ?? '—'}</span>
    </div>
  `;

  return banner + rowsHtml + guardDetail + execDetail + caps;
}

// Called from data-service.js's scheduleRender() the same way
// renderPaperTradingPanel() is — see DataService.updateDashboard() /
// scheduleRender()'s doRender(). Cheap no-op guard (mirrors setHtmlIfChanged)
// keeps this from doing DOM work on every tick when algoStatus itself
// hasn't changed, since algo_status_loop() broadcasts on its own slow
// timer independent of the tick-rate market data arrives at.
function renderAlgoStatusPanel(wsState){
  if(!wsState) return;
  const btn = $i('algo-toggle-btn');
  const body = $i('algo-panel-body');
  if(!btn || !body) return; // not mounted yet

  const status = wsState.algoStatus;
  if(!status) return; // no algoStatus message received on this connection yet

  const killActive = !!status.killSwitchActive;
  const guardTripped = !!(status.accountGuard && status.accountGuard.tripped);
  const liveOn = !!status.liveTradingEnabled;

  // Button itself reflects the worst-case state at a glance, without
  // needing the panel open — mirrors paper-trading's mode pill doing the
  // same for paper/live.
  btn.classList.toggle('algo-tripped', killActive || guardTripped);
  btn.classList.toggle('algo-live', liveOn && !killActive && !guardTripped);
  btn.innerHTML = (killActive || guardTripped) ? '<span>⛔</span>Algo' : (liveOn ? '<span>🔴</span>Algo' : '<span>🤖</span>Algo');

  setHtmlIfChanged(body, buildAlgoStatusHtml(status));
}
window.renderAlgoStatusPanel = renderAlgoStatusPanel;

// ── Reconciliation alerts ────────────────────────────────────────────
// Fed by {"type":"reconciliationAlert","payload":{...}} —
// ws_server_live.py's _broadcast_reconciliation_alert() fires this
// whenever risk/position_reconciler.py's periodic sweep (reconcile_loop)
// or its post-fill check finds the live order book and position book
// disagree, EVEN BELOW the lot threshold that actually trips the kill
// switch (see position_reconciler.py's module docstring — most of these
// resolve themselves next cycle, but a human watching should still see
// them as cheap early signals rather than only learning about it once a
// trip already happened). MarketStore.ingest()'s generic branch lands
// this at wsState.reconciliationAlert for free, same as algoStatus.
//
// Deliberately its own toast host, not paper-trading.js's #pt-toast-wrap
// — mount order between the two files' DOMContentLoaded listeners isn't
// guaranteed, and this needs to survive independently of whether the
// paper trading panel happens to be mounted.
function reconMountToastHost(){
  if($i('recon-toast-wrap')) return;
  const wrap = document.createElement('div');
  wrap.id = 'recon-toast-wrap';
  document.body.appendChild(wrap);
}

function _reconToast(message, kind){
  const wrap = $i('recon-toast-wrap');
  if(!wrap) return;
  const el = document.createElement('div');
  el.className = 'recon-toast ' + (kind === 'err' ? 'recon-toast-err' : 'recon-toast-warn');
  el.textContent = message;
  wrap.appendChild(el);
  // Trip alerts (kill switch actually fired) stay up longer than a plain
  // below-threshold mismatch — the latter is a "just so you know", the
  // former is the thing account_guard trips also require a human to
  // manually clear (delete the kill-switch file), so it deserves more
  // than a quick flash.
  const ttl = kind === 'err' ? 9000 : 5000;
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity .2s';
    setTimeout(() => el.remove(), 220);
  }, ttl);
}

function _reconFmtMismatch(m){
  const sign = n => (n > 0 ? '+' : '') + n;
  return `${m.symbol} off by ${sign(m.diffLots)} lot${Math.abs(m.diffLots)===1?'':'s'} `
       + `(order book ${sign(m.orderBookLots)} vs positions ${sign(m.positionLots)})`;
}

// Dedupe by ts — algo_status-style periodic broadcasts share the same
// payload shape every cycle, and ts (server-side time.time()) is unique
// per check, so a client that's had this alert in wsState since before
// this tab was open (initial-connection replay) doesn't re-toast on
// every render pass. Same "seen key" pattern as paper-trading.js's
// ptNotifyNewRejections().
const _reconSeenTs = new Set();
function renderReconciliationAlerts(wsState){
  if(!wsState) return;
  const alert = wsState.reconciliationAlert;
  if(!alert || _reconSeenTs.has(alert.ts)) return;
  _reconSeenTs.add(alert.ts);

  const kind = alert.tripped ? 'err' : 'warn';
  const sourceLabel = alert.source === 'post_fill' ? 'post-fill check' : 'periodic check';

  if(alert.tripped){
    const worst = (alert.mismatches || []).slice().sort((a,b) => Math.abs(b.diffLots) - Math.abs(a.diffLots))[0];
    _reconToast(`⛔ Position reconciliation TRIPPED the kill switch — ${worst ? _reconFmtMismatch(worst) : 'see server log'}`, 'err');
  } else if(alert.mismatches && alert.mismatches.length){
    alert.mismatches.forEach(m => {
      _reconToast(`⚠ Reconciliation mismatch (${sourceLabel}): ${_reconFmtMismatch(m)}`, kind);
    });
  }
  if(alert.unparseableSymbols && alert.unparseableSymbols.length){
    _reconToast(`⚠ Reconciliation (${sourceLabel}) couldn't parse: ${alert.unparseableSymbols.join(', ')}`, 'warn');
  }
}
window.renderReconciliationAlerts = renderReconciliationAlerts;

window.addEventListener('DOMContentLoaded', algoMountPanel);
window.addEventListener('DOMContentLoaded', reconMountToastHost);
