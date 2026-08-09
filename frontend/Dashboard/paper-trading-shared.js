// ── paper-trading-shared.js ─────────────────────────────────────────────
// Split 2026-08-05 out of the old monolithic paper-trading.js (1838 lines)
// into three files: this one (state/constants/helpers shared by both
// panels), order-entry.js (#pt-order-panel: form, basket, quick order,
// strategy execution), and portfolio-tracker.js (#pt-portfolio-panel:
// P&L/fund summary, positions table, orders table, mount orchestrator).
//
// Load order in DashboardPro.html: this file first, then order-entry.js,
// then portfolio-tracker.js. Nothing here is called at parse time (all
// top-level code below is either a const/function declaration or a
// DOM-independent computation), so — same as every other pt-* global in
// this codebase — cross-file references inside function bodies resolve
// fine regardless of exact <script> order, as long as all three are
// loaded before renderPaperTradingPanel()/ptMountPanel() actually run
// (i.e. before DOMContentLoaded).
//
// What stays split out (NOT here):
//   - Anything that only reads/writes #pt-order-panel's own form fields
//     (symbol/expiry/strike/side/qty/order-type/basket) → order-entry.js
//   - Anything that only computes/paints #pt-portfolio-panel's tables
//     (P&L summary, positions, orders/trade log) → portfolio-tracker.js
// What stays here: constants and pure calculations both panels need
// (lot sizes, charges, fund summary, formatting), the order dispatch
// pipeline (both "Place Order" and "Square Off" send through it), the
// pending-order tracking list, live/paper mode (touches both panels'
// mode badges), toast notifications, and the two panels' shared
// positioning/open/close mechanics.

function sendWsMessage(type, payload){
  if(!_ws || _ws.readyState !== WebSocket.OPEN){
    err('WS not connected — cannot send ' + type);
    return false;
  }
  try{
    _ws.send(JSON.stringify({type, payload}));
    return true;
  }catch(e){
    err('WS send error: ' + e.message);
    return false;
  }
}
window.sendWsMessage = sendWsMessage;

// Lot sizes are resolved server-side (paper_trading.py's get_lot_size(),
// which reads the live AngelOne instrument master via
// smartapi_instruments.py) rather than duplicated here as a static table —
// a hardcoded copy silently goes wrong the moment NSE revises a lot size,
// or for any symbol (stock F&O, BANKEX, SENSEX50, ...) that was never
// added to the hand-maintained list.
//
// PT_LOT_SIZES is a client-side CACHE, not a source of truth: it starts
// empty and fills in as ptGetLotSize() below resolves each symbol.
const PT_LOT_SIZES = {};

// First-paint fallback before the backend's full fnoSymbols universe arrives.
// The Order selector replaces this list with grouped indices/stocks as soon
// as a normal dashboard payload is received.
const PT_KNOWN_SYMBOLS = ['NIFTY', 'BANKNIFTY', 'MIDCPNIFTY', 'SENSEX', 'FINNIFTY'];

// Only used if the backend lookup has never succeeded for this symbol AND
// there's nothing cached yet (e.g. page just loaded, request in flight or
// failed). Deliberately small — anything not in here fails loud (returns
// null) instead of quietly pricing/margining against a guessed lot size.
// Emergency-only until /api/lot-sizes responds (FUT-derived server map).
// Keep roughly aligned with a recent master snapshot — never treat as source of truth.
const PT_LOT_SIZE_HARDCODED_FALLBACK = { NIFTY:65, BANKNIFTY:30, MIDCPNIFTY:120, SENSEX:20, FINNIFTY:65 };

// Synchronous lookup for hot paths (charge calcs, PnL, table renders).
// Returns the cached value if we have one, else the narrow static
// fallback, else null (caller must handle "unknown" rather than silently
// treating it as 1 lot, which was the previous bug).
function ptGetLotSize(symbol){
  if (PT_LOT_SIZES[symbol] != null) return PT_LOT_SIZES[symbol];
  if (PT_LOT_SIZE_HARDCODED_FALLBACK[symbol] != null) return PT_LOT_SIZE_HARDCODED_FALLBACK[symbol];
  return null;
}

// Every call site below used to do `ptGetLotSize(x) || 1`, which silently
// re-introduced the exact bug the null-return above was meant to prevent:
// any symbol that isn't one of the 5 indices in PT_LOT_SIZE_HARDCODED_FALLBACK
// (i.e. every stock F&O name, BANKEX, SENSEX50, ...) priced/PnL'd/margined
// against a lot size of 1 until /api/lot-sizes happened to resolve it —
// wrong by whatever that symbol's real lot size is (e.g. off by 250x for a
// stock with lot size 250), with nothing on screen indicating the number
// was unreliable. Route unresolved lookups through here instead: log once
// per symbol (not once per render) and let each caller decide how to
// represent "unknown" (skip the contribution, show "—", skip a live
// reprice), rather than guessing 1.
const _ptLotWarned = new Set();
function ptWarnUnresolvedLot(symbol){
  if (_ptLotWarned.has(symbol)) return;
  _ptLotWarned.add(symbol);
  Logger.warn('paper-trading', 'lot size not yet resolved for "' + symbol + '" — skipping this value rather than guessing 1. Will self-correct once /api/lot-sizes responds.');
}

// Populates PT_LOT_SIZES from the backend. Wire this to whatever endpoint
// exposes paper_trading.get_lot_size() per symbol — e.g. a REST route like
// GET /api/lot-sizes returning {"NIFTY":75,"BANKNIFTY":35,...}, or a WS
// request/response message if ws_server_live.py already has a channel for
// this. Call once on panel init, and re-call periodically (e.g. once at
// market open) since lot sizes can change on NSE's quarterly review.
async function ptRefreshLotSizes(){
  try{
    const res = await fetch(Config.api.lotSizes);
    if(!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    Object.assign(PT_LOT_SIZES, data);
    ptUpdateLotSizeHint();
  }catch(e){
    Logger.warn('paper-trading', 'lot size refresh failed, using cached/fallback values', e);
  }
}

// ── Fund / available margin (paper-trading estimate) ───────────────────
// paper_trading.py's portfolio payload only carries realized/unrealized
// P&L per position — there's no backend "capital" or "margin" concept to
// read. This block is a client-side approximation so Profit and Fund can
// be surfaced at a glance (top-bar pill + Paper Trading panel) without
// waiting on a backend margin engine. If/when paper_trading.py starts
// sending real capital/margin figures on the portfolio payload, wire
// those in here instead of estimating.
const PT_STARTING_CAPITAL = 100000; // ₹1,00,000 paper capital
// Real SPAN+exposure margin for short/written options isn't modeled
// anywhere in this app — approximated as a flat % of notional (spot ×
// lot × qty). This is intentionally rough; treat "Fund" as a quick
// sanity check ("am I close to zero, should I square off?"), not an
// exact margin figure a real broker would quote.
const PT_SHORT_MARGIN_PCT = 0.12;
// Fund below this fraction of starting capital flags the top-bar pill
// and panel red as a "consider squaring off" signal.
const PT_LOW_FUND_PCT = 0.20;

// ── Statutory / regulatory charges (NSE F&O — Index Options) ───────────
// The Realized/Unrealized/Total P&L above are pure (LTP − avg_price) ×
// qty × lot_size — i.e. gross mark-to-market only. That number alone
// overstates actual profit (and understates actual loss), because every
// FILLED leg also incurs statutory charges that a real broker would
// deduct. These rates are the standard NSE/SEBI/government schedule for
// index options, current as of the Budget 2026 STT revision (effective
// 1 April 2026: STT on options premium raised to 0.15%, from the 0.10%
// set by the earlier Budget 2024 revision). They're set by regulation,
// not by this app, and do change — revisit periodically rather than
// treating as permanent. Brokerage is a flat ₹20/order (matching the
// broker's actual per-order charge) — GST below already applies to it
// along with the exchange txn charge and SEBI fee.
const PT_CHARGE_RATES = {
  sttSellRate:      0.0015,     // STT: 0.15% of premium, SELL side only
                                 // (raised from 0.10% by Budget 2026, effective
                                 // 1 April 2026 — also applies to exercised/ITM
                                 // options, previously a separate 0.125% rate on
                                 // intrinsic value; both now unified at 0.15%.
                                 // Was 0.10% under the earlier Budget 2024 revision.)
  exchangeTxnRate:  0.0003503,  // NSE transaction charge: 0.03503% of premium, both sides
  sebiFeeRate:      0.000001,   // SEBI turnover fee: ₹10/crore = 0.0001% of premium, both sides
  stampDutyRate:    0.00003,    // Stamp duty: 0.003% of premium, BUY side only
  gstRate:          0.18,       // GST: 18% on (exchange txn charge + SEBI fee + brokerage)
  brokeragePerOrder:20,         // flat ₹20/order brokerage (matches broker's actual charge)
};

// Per-leg statutory charges on one FILLED order's premium turnover
// (price × qty_lots × lot_size). Returns the breakdown plus a `total`,
// so callers can either show the full breakdown or just net it off P&L.
function ptCalcCharges(premium, qtyLots, lotSize, side){
  const turnover = Math.max(0, Number(premium)||0) * Math.max(0, Number(qtyLots)||0) * Math.max(0, Number(lotSize)||0);
  if(!turnover) return { stt:0, exchangeTxn:0, sebiFee:0, stampDuty:0, gst:0, brokerage:0, total:0 };
  const stt         = side === 'SELL' ? turnover * PT_CHARGE_RATES.sttSellRate : 0;
  const exchangeTxn = turnover * PT_CHARGE_RATES.exchangeTxnRate;
  const sebiFee     = turnover * PT_CHARGE_RATES.sebiFeeRate;
  const stampDuty   = side === 'BUY' ? turnover * PT_CHARGE_RATES.stampDutyRate : 0;
  const brokerage   = PT_CHARGE_RATES.brokeragePerOrder;
  const gst         = (exchangeTxn + sebiFee + brokerage) * PT_CHARGE_RATES.gstRate;
  const total = stt + exchangeTxn + sebiFee + stampDuty + gst + brokerage;
  return { stt, exchangeTxn, sebiFee, stampDuty, gst, brokerage, total };
}

// Sum statutory charges across every FILLED order in the account's full
// history (not just the last-15 slice the trade log displays) — charges
// are incurred at execution time on each leg, whether the position it
// opened is still open (unrealized) or has since been squared off
// (realized), so this is netted against Total P&L, not Realized alone.
function ptTotalCharges(orders){
  return (orders || []).reduce((sum, o)=>{
    if(String(o.status||'').toUpperCase() !== 'FILLED') return sum;
    const lot = ptGetLotSize(o.symbol);
    if(lot == null){ ptWarnUnresolvedLot(o.symbol); return sum; }
    const premium = o.fill_price ?? o.limit_price ?? 0;
    return sum + ptCalcCharges(premium, o.qty_lots, lot, o.side).total;
  }, 0);
}

// ptTotalCharges only counts charges on legs that have actually executed —
// for an OPEN position that's just the entry leg. It never includes the
// exit leg, because no exit order exists yet. That's correct for "charges
// incurred so far," but understates what a position would actually cost
// to close: the exit leg (especially SELL-side STT) hasn't been charged
// yet either. This estimates that hypothetical exit-leg charge, at each
// position's current LTP, so "if I squared off everything right now" can
// be shown as its own (explicitly estimated) figure rather than silently
// baked into — or silently missing from — the main Net P&L line. A real
// MARKET order can still fill at a slightly different price than the
// LTP shown here, so treat this as an estimate, not a guarantee.
function ptEstimateExitCharges(positions){
  return (positions || []).reduce((sum, p)=>{
    if(!p.net_qty_lots) return sum;
    const lot = ptGetLotSize(p.symbol);
    if(lot == null){ ptWarnUnresolvedLot(p.symbol); return sum; }
    const exitPrice = p.last_price ?? p.avg_price ?? 0;
    const exitSide = p.net_qty_lots > 0 ? 'SELL' : 'BUY'; // opposite of the open side
    return sum + ptCalcCharges(exitPrice, Math.abs(p.net_qty_lots), lot, exitSide).total;
  }, 0);
}

// Approximate margin currently locked up by open positions:
//  - Long (net_qty_lots > 0): the premium already paid — that cash is
//    spent, not "blocked", but it's no longer available either way.
//  - Short/written (net_qty_lots < 0): no real SPAN+exposure calc exists
//    in this app, so approximated as PT_SHORT_MARGIN_PCT of notional
//    (current spot × lot size × qty). See the PT_SHORT_MARGIN_PCT comment
//    above for why this is a rough estimate, not a broker-accurate figure.
function ptEstimateMarginBlocked(positions, wsState){
  const spot = Number(wsState && wsState.spot) || 0;
  return (positions || []).reduce((sum, p) => {
    const qty = Math.abs(p.net_qty_lots || 0);
    if (!qty) return sum;
    const lot = ptGetLotSize(p.symbol);
    if (lot == null) { ptWarnUnresolvedLot(p.symbol); return sum; }
    if (p.net_qty_lots > 0) {
      return sum + (Number(p.avg_price) || 0) * qty * lot;
    }
    const notional = (spot || Number(p.avg_price) || 0) * qty * lot;
    return sum + notional * PT_SHORT_MARGIN_PCT;
  }, 0);
}

// Backend-agnostic "live portfolio" fix: paper_trading.py's last_price /
// unrealized_pnl on each position only reflect whatever LTP it had at
// the time it last recomputed (typically on order/fill events). Rather
// than wait for a backend change, re-price every open CE/PE/FUT/INDEX
// position here against whatever chain/spot data this tick's AppState.wsState
// already carries, so the portfolio panel tracks the live market tick
// by tick instead of freezing between orders.
//
// Used by both ptComputeFundSummary (below, for the top-bar pill) and
// portfolio-tracker.js's ptComputePortfolioView (for the panel's own P&L
// strip/positions table) — both need the same repriced positions so the
// two can never drift out of sync with each other.
function ptLiveReprice(pf, d){
  if(!pf || !pf.positions || !d) return pf;
  pf.positions.forEach(p=>{
    let liveLtp = null;
    if(p.instrument_type === 'INDEX'){
      liveLtp = parseFloat(d.spot) || null;
    } else if(p.instrument_type === 'CE' || p.instrument_type === 'PE'){
      let rows = (d.chains && p.expiry && d.chains[p.expiry]) ? d.chains[p.expiry]
        : ((!p.expiry || p.expiry === d.expiry) ? (d.chain||[]) : []);
      const row = rows.find(r=>r.strike === p.strike);
      if(row) liveLtp = p.instrument_type === 'CE' ? row.ceLTP : row.peLTP;
    }
    // Normalize before comparing — a stray case/whitespace difference
    // between the position's symbol (from the backend portfolio payload)
    // and the tick's active symbol (from the WS stream) would silently
    // fail this check on every tick, making the position never take the
    // fast per-tick reprice path below and instead only ever update
    // whenever paper_trading.py recomputes the portfolio server-side —
    // i.e. it would look "slow" and tied to the backend's own refresh
    // cadence instead of the live tick stream. Logged once per symbol
    // pair so a genuine mismatch is easy to spot in devtools.
    const symMatches = String(p.symbol||'').trim().toUpperCase() === String(d.symbol||'').trim().toUpperCase();
    if(liveLtp != null && !symMatches){
      const key = '_ptSymMismatchLogged_' + p.symbol + '_' + d.symbol;
      if(!window[key]){
        window[key] = true;
        Logger.warn('paper-trading', 'position symbol "'+p.symbol+'" did not match active tick symbol "'+d.symbol+'" — live reprice skipped for this position. If these are supposed to be the same symbol, check for case/whitespace differences at the source.');
      }
    }
    if(liveLtp != null && symMatches){
      const lot = ptGetLotSize(p.symbol);
      if(lot == null){
        ptWarnUnresolvedLot(p.symbol);
      } else {
        p.last_price = liveLtp;
        p.unrealized_pnl = (liveLtp - p.avg_price) * p.net_qty_lots * lot;
        p._live = true;
      }
    }
  });

  pf.unrealized_pnl = pf.positions.reduce((s,p)=>s+(p.unrealized_pnl||0), 0);
  pf.total_pnl = (pf.realized_pnl||0) + pf.unrealized_pnl;
  return pf;
}

// Single entry point for "Profit and Fund at a glance" — used by both the
// top-bar pill (chain-views.js's renderTopBarHtml) and the Paper Trading
// panel below. Returns null until the backend's portfolio feed exists,
// matching the same guard renderPaperTradingPanel() already uses.
//
// capital = starting capital, running with ALL-TIME net P&L (gross P&L
// minus statutory charges across full order history — not just the
// since-last-Reset slice the trade log/Charges line show, since Fund
// must keep tracking true available money regardless of that cosmetic
// reset). fund = capital minus margin currently tied up in open
// positions — i.e. what's actually free to place a new order with.
//
// LIVE MODE: there is currently no wiring anywhere (frontend or, as far
// as this file can see, backend) that fetches real AngelOne account
// funds/margin — _ptLiveMode only changes the per-order confirmation
// flow, it was never connected to a real funds source. So when live mode
// is on, `fund`/`marginBlocked`/`lowFund` are deliberately nulled out
// here instead of continuing to show the ₹1,00,000 paper-capital number,
// which would look like a real balance but isn't one. netPnl/capital
// are left as-is (still just the paper P&L model) but callers should
// label them "(paper)" while live — see fundSource below. Once a real
// funds fetch exists (e.g. a `{type:"funds",...}` WS message populating
// wsState.funds from smartapi_client.py's rmsLimit()), this is the place
// to switch `fund` over to that real figure when isLive is true.
function ptComputeFundSummary(wsState){
  if (!wsState || !wsState.portfolio) return null;
  const pf = ptLiveReprice(wsState.portfolio, wsState);
  const totalCharges = ptTotalCharges(wsState.orders || []);
  const netPnl = (pf.total_pnl || 0) - totalCharges;
  const capital = PT_STARTING_CAPITAL + netPnl;
  const marginBlocked = ptEstimateMarginBlocked(pf.positions || [], wsState);
  const fund = capital - marginBlocked;
  if (_ptLiveMode) {
    // Real account funds arrive as wsState.funds — a generic
    // `{type:"funds", payload:{...}}` WS message that Dashboard.js's
    // deepMerge(AppState.wsState, {[msg.type]: msg.payload}) already lands there
    // automatically, same as .portfolio/.chain/etc, no extra frontend
    // wiring needed once the backend actually sends it (see
    // smartapi_client.py's get_funds(), which wraps rmsLimit() — as of
    // this commit ws_server_live.py still needs to call that and
    // broadcast it; until then this branch is never hit and Live mode
    // falls through to the "unavailable" state below).
    const rf = wsState.funds;
    if (rf) {
      const realNetPnl = (Number(rf.m2m_realized) || 0) + (Number(rf.m2m_unrealized) || 0);
      return {
        netPnl: realNetPnl,
        capital: (Number(rf.available_cash) || 0) + (Number(rf.utilised_margin) || 0),
        marginBlocked: Number(rf.utilised_margin) || 0,
        fund: Number(rf.available_margin ?? rf.available_cash) || 0,
        // No auto "low fund" flag for real money yet — the 20% paper
        // threshold above means nothing against a real account's actual
        // risk limits, and guessing at one could give false comfort or a
        // false alarm with real capital on the line. Add a real
        // threshold here once you tell me what should trigger it.
        lowFund: false,
        isLive: true,
        fundSource: 'live-real'
      };
    }
    return {
      netPnl, capital,
      marginBlocked: null,
      fund: null,
      lowFund: false,
      isLive: true,
      fundSource: 'live-unavailable'
    };
  }
  const backendFunds = wsState.portfolio.funds;
  if(backendFunds){
    return {
      netPnl: Number(backendFunds.realized_pnl||0) + Number(backendFunds.unrealized_pnl||0),
      capital: Number(backendFunds.equity ?? backendFunds.capital) || 0,
      marginBlocked: Number(backendFunds.margin_blocked) || 0,
      fund: Number(backendFunds.fund) || 0,
      lowFund: !!backendFunds.low_fund,
      isLive: false,
      fundSource: 'paper-backend'
    };
  }
  return {
    netPnl, capital, marginBlocked, fund,
    lowFund: fund < PT_STARTING_CAPITAL * PT_LOW_FUND_PCT,
    isLive: false,
    fundSource: 'paper-estimate'
  };
}
window.ptComputeFundSummary = ptComputeFundSummary;

function ptFmtN(n, d){
  if(n===null||n===undefined||n===''||isNaN(n)) return '—';
  return Number(n).toLocaleString('en-IN', {minimumFractionDigits:d??2, maximumFractionDigits:d??2});
}

// Escapes text for safe use inside an HTML attribute (e.g. a rejection
// reason dropped into data-reason="..."). Reasons come from the backend
// and may contain quotes/HTML-ish characters we don't control.
function ptEscAttr(s){
  return escapeHtml(s);
}

function ptPnlClass(v){
  if(v===null||v===undefined||isNaN(v)) return '';
  return v > 0 ? 'pt-pos' : (v < 0 ? 'pt-neg' : '');
}

// Compact "DD-Mon" rendering of the "DD-Mon-YYYY" expiry strings used
// throughout the option chain — expiry is the single most important field
// for a CE/PE/FUT order or position (and the defining field of a calendar
// spread, where two legs share a strike but differ ONLY by expiry), so it
// must never be silently dropped from the trade log / positions tables.
function ptFmtExpiry(expiry){
  if(!expiry) return '—';
  const parts = String(expiry).split('-');
  return parts.length >= 2 ? (parts[0]+'-'+parts[1]) : expiry;
}

function ptToast(message, kind){
  const wrap = $i('pt-toast-wrap');
  if(!wrap) return;
  const el = document.createElement('div');
  el.className = 'pt-toast ' + (kind==='err' ? 'pt-toast-err' : 'pt-toast-ok');
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(()=>{ el.style.opacity='0'; el.style.transition='opacity .2s'; setTimeout(()=>el.remove(), 220); }, 3500);
}

// Global paper/live mode switch. Deliberately NOT persisted to
// localStorage/sessionStorage — every fresh page load starts back in
// Paper mode. A page reload should never silently carry live mode forward;
// re-enabling it is a deliberate action the person has to take again each
// session.
//
// Lives here (not in order-entry.js) because it's read by ptComputeFundSummary
// and ptDispatchOrder above/below, and because toggling it updates DOM in
// BOTH panels (#pt-mode-toggle pill on the order panel, #pt-portfolio-mode-badge
// on the portfolio panel) — it's genuinely cross-panel state, not
// order-entry-specific.
let _ptLiveMode = false;
function ptIsLiveMode(){ return _ptLiveMode; }
window.ptIsLiveMode = ptIsLiveMode;

function ptToggleLiveMode(){
  if(!_ptLiveMode){
    // Turning ON live mode is itself a deliberate step, separate from the
    // per-order confirm modal below — this is "arm the mechanism", the
    // per-order modal is "fire it". A native confirm() is enough friction
    // here since the actual money-movement gate is the per-order modal.
    const ok = confirm(
      '⚠ You are about to enable LIVE trading mode.\n\n' +
      'While enabled, every order you place will show a confirmation ' +
      'asking whether to send it as a REAL order to your AngelOne account.\n\n' +
      'Continue?'
    );
    if(!ok) return;
  }
  _ptLiveMode = !_ptLiveMode;
  const pill = $i('pt-mode-toggle');
  const panel = $i('pt-order-panel');
  // #pt-toggle-btn is the dedicated "Portfolio" rail button now (see
  // DashboardPro.html's 2026-08-04 order/portfolio split) — it no longer
  // doubles as a paper/live mode indicator, so its label is left alone.
  // The portfolio panel's own mode readout (pt-portfolio-mode-badge) is
  // synced here instead, mirroring the order panel's pt-mode-toggle pill.
  const portfolioBadge = $i('pt-portfolio-mode-badge');
  if(pill){
    pill.textContent = _ptLiveMode ? '🔴 LIVE' : '📝 PAPER';
    pill.classList.toggle('live', _ptLiveMode);
    pill.classList.toggle('paper', !_ptLiveMode);
  }
  if(portfolioBadge){
    portfolioBadge.textContent = _ptLiveMode ? '🔴 LIVE' : '📝 PAPER';
    portfolioBadge.classList.toggle('live', _ptLiveMode);
    portfolioBadge.classList.toggle('paper', !_ptLiveMode);
  }
  const title = $i('pt-panel-title');
  if(title) title.textContent = _ptLiveMode ? 'Live Trading' : 'Paper Trading';
  if(panel) panel.classList.toggle('live-mode', _ptLiveMode);
  ptToast(_ptLiveMode ? 'LIVE trading mode enabled' : 'Back to Paper trading mode', _ptLiveMode ? 'err' : 'ok');
  // Directs the socket to start/stop real funds polling, the same way
  // switching the top-bar symbol dropdown directs it to switch feeds —
  // no server restart involved. This ONLY controls funds polling; it does
  // NOT enable real order placement, which stays gated server-side by
  // LIVE_TRADING_ENABLED (a deliberate restart-only decision — see
  // ws_server_live.py) regardless of what this sends.
  sendWsMessage('toggle_live_mode', { enabled: _ptLiveMode });
  if(AppState.wsState) renderPaperTradingPanel(AppState.wsState);
}
window.ptToggleLiveMode = ptToggleLiveMode;

// Mirrors ui-controls.js's toggleControlSidebar() / algo-status.js's
// toggleAlgoPanel(): the panel isn't docked to a fixed corner anymore,
// so position it next to wherever the "Paper" rail button actually is,
// clamped so it can never render partially off-screen. #pt-panel is
// wide (min(660px, 96vw)), so this is what most needs the clamp — a
// naive right-of-button placement would routinely run off the right
// edge on anything narrower than a wide desktop viewport.
// Shared positioning helper for the two split panels (order/portfolio) —
// opens `panelId` next to `btnId`, clamped so it never renders partially
// off-screen. Extracted from the old single-panel togglePtPanel() when
// the panel was split 2026-08-04; #pt-order-panel/#pt-portfolio-panel
// are each min(660px, 96vw) wide, so this is what most needs the clamp.
// Keeps the rail button's .active state in sync with whether its panel is
// currently open. Previously #pt-order-toggle-btn / #pt-toggle-btn looked
// identical whether the panel was open or closed — the only way to tell
// which (if either) was open was to spot the floating panel itself, easy
// to lose track of since panels are positioned dynamically next to the
// rail rather than docked to it. Reuses .sec-btn.active (navigation.css) —
// the same left-edge accent bar + glow already used for section-nav
// selection — so both buttons get a "this window is open" indicator for
// free, no new CSS needed, as long as they carry the shared .sec-btn class
// (see DashboardPro.html; they're documented as sec-btns in #sec-nav-bar).
function ptSyncToggleBtnActive(panelId, btnId){
  const panel = $i(panelId), btn = $i(btnId);
  if(!panel || !btn) return;
  btn.classList.toggle('active', panel.classList.contains('open'));
}

// Single close path for both panels' ✕ buttons, so closing always also
// clears the rail button's .active state — closing used to be a bare
// classList.remove('open') inline on the panel only, which would have
// left the button showing "open" after the user explicitly closed it.
function ptClosePanel(panelId, btnId){
  const panel = $i(panelId);
  if(panel) panel.classList.remove('open');
  ptSyncToggleBtnActive(panelId, btnId);
}
window.ptClosePanel = ptClosePanel;

function ptTogglePanelNear(panelId, btnId){
  const el = $i(panelId);
  if(!el) return;
  const opening = !el.classList.contains('open');
  el.classList.toggle('open');
  ptSyncToggleBtnActive(panelId, btnId);
  if(opening){
    const proxyBtn = $i(btnId);
    const btn = proxyBtn && proxyBtn.offsetParent ? proxyBtn : $i('rail-tools-toggle');
    if(btn){
      const r = btn.getBoundingClientRect();
      const vw = window.innerWidth, vh = window.innerHeight;
      const EDGE_MARGIN = 16;
      let left = Math.max(r.right + 8, EDGE_MARGIN);
      let top  = r.top;
      requestAnimationFrame(()=>{
        const pw = el.offsetWidth || 660;
        const ph = el.offsetHeight || 400;
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

// ── Pending-order tracking + order dispatch pipeline ────────────────────
// Both panels feed into this: the order form/basket/quick-popover/strategy
// execution (order-entry.js) all send NEW orders through ptDispatchOrder,
// and the Positions table's per-row exit / "Square Off All" buttons
// (portfolio-tracker.js) also flatten positions by sending an opposite-side
// order through the same pipeline — so toasts, the pending-row placeholder,
// and the live-mode confirmation modal behave identically no matter which
// panel an order originated from.
let _ptPending = []; // {id, symbol, side, qty_lots, order_type, limit_price, strike, instrument_type, ts, status}

// A pending row and its eventual backend confirmation don't share an id
// at send time (the id only exists once the backend assigns one), so
// match on the order's identifying fields instead: same instrument, same
// side/qty/type, confirmed at-or-after the moment it was sent (with a
// couple seconds' grace for clock skew between browser and server).
function ptFindMatchingConfirmedOrder(pending, orders){
  return (orders || []).some(o=>{
    if(pending.client_order_id && o.client_order_id){
      return pending.client_order_id === o.client_order_id;
    }
    const tsVal = o.fill_timestamp ?? o.timestamp;
    const tsMs = tsVal ? tsVal * 1000 : null;
    if(tsMs != null && tsMs < pending.ts - 2000) return false;
    return o.symbol === pending.symbol
      && o.side === pending.side
      && o.instrument_type === pending.instrument_type
      && (o.strike ?? null) === (pending.strike ?? null)
      && (o.expiry || '') === (pending.expiry || '')
      && Number(o.qty_lots) === Number(pending.qty_lots)
      && o.order_type === pending.order_type;
  });
}

function _ptSendOrderNow(payload, errEl, btn){
  // Stable, broker-compatible submission identity: paper orders deduplicate
  // durably, while live retries reuse the AngelOne order tag and cannot
  // submit the same confirmed intent twice. Keep it alphanumeric and <=20.
  if(!payload.client_order_id){
    payload.client_order_id = (payload.live ? 'l' : 'p')
      + Date.now().toString(36) + Math.random().toString(36).slice(2,10);
  }
  const ok = sendWsMessage('place_order', payload);
  const priceBit = {
    'LIMIT': ptFmtN(payload.limit_price,2),
    'SL':    'trig ' + ptFmtN(payload.trigger_price,2) + ' → lim ' + ptFmtN(payload.limit_price,2),
    'SL-M':  'trig ' + ptFmtN(payload.trigger_price,2) + ' → MKT',
    'TSL':   'trail ' + ptFmtN(payload.trail_value,2) + ' pts',
    'GTT':   'trig ' + ptFmtN(payload.trigger_price,2) + ' → lim ' + ptFmtN(payload.limit_price,2)
             + ' (valid ' + payload.gtt_expiry_days + 'd)',
  }[payload.order_type] || 'MKT';
  const label = payload.symbol + ' ' + (payload.strike ? payload.strike+' '+payload.instrument_type : payload.instrument_type)
    + ' — ' + payload.side + ' ' + payload.qty_lots + ' lot' + (payload.qty_lots===1?'':'s')
    + ' @ ' + priceBit;
  if(ok){
    const pending = Object.assign({}, payload, {id:payload.client_order_id||('pend_'+Date.now()+'_'+Math.random().toString(36).slice(2)), status:'SUBMITTED', ts:Date.now()});
    _ptPending.unshift(pending);
    // BUGFIX: this used to unconditionally delete the pending row after
    // 10s ("by then the real order should have arrived and superseded
    // it") — but if the backend never actually confirms (an order_type
    // it doesn't recognize yet, like SL/SL-M/TSL/GTT before the backend
    // side is wired up, or a dropped WS message), that deletion made the
    // order look like it vanished into nothing: SENT, then nothing, no
    // error, no trace. Now it only clears if a matching confirmed order
    // has actually shown up; otherwise it flips to a visible TIMEOUT
    // state instead of disappearing, so "sent but never confirmed" is
    // always distinguishable from "confirmed and no longer needs the
    // placeholder row."
    setTimeout(()=>{
      const row = _ptPending.find(p=>p.id===pending.id);
      if(row){
        if(ptFindMatchingConfirmedOrder(row, (AppState.wsState && AppState.wsState.orders) || [])){
          _ptPending = _ptPending.filter(p=>p.id!==pending.id);
        } else {
          row.status = 'TIMEOUT';
        }
        if(AppState.wsState) renderPaperTradingPanel(AppState.wsState);
      }
    }, 10000);
    ptToast((payload.live ? '🔴 LIVE — ' : '') + label + ' — sent', 'ok');
    if(errEl){
      errEl.style.color = 'var(--pos,#2ecc71)';
      errEl.textContent = 'Order sent';
      setTimeout(()=>{ if(errEl.textContent==='Order sent') errEl.textContent=''; }, 2000);
    }
    // Make sure the confirmation + orders table are actually visible —
    // this was the core of "no trade/order information after order
    // sent": ordering from the option-chain popover left the panel
    // closed the whole time.
    const panel = $i('pt-order-panel');
    if(panel) panel.classList.add('open');
    ptSyncToggleBtnActive('pt-order-panel', 'pt-order-toggle-btn');
    if(AppState.wsState) renderPaperTradingPanel(AppState.wsState);
  } else {
    ptToast(label + ' — failed to send (WS not connected)', 'err');
    if(errEl) errEl.textContent = 'WS not connected — order not sent';
    if(btn && btn.setError) btn.setError('WS not connected');
  }
  return ok;
}

// Single choke point every order path (main form, quick-order popover,
// strategy legs, square-off) routes through. In Paper mode this just
// forwards straight to _ptSendOrderNow() — behavior is byte-for-byte
// identical to before live trading existed. In Live mode, it intercepts
// here and shows a per-order confirmation modal; the actual send (with
// payload.live=true, payload.confirmed=true set) only happens if the
// person explicitly clicks "Place Real Order". Cancelling sends nothing
// at all — not even a paper order — since the person's intent was to
// place a live order and back out, not to silently fall back to paper.
function ptDispatchOrder(payload, errEl, btn){
  if(!_ptLiveMode){
    return _ptSendOrderNow(payload, errEl, btn);
  }

  const priceBit = {
    'LIMIT': ptFmtN(payload.limit_price,2),
    'SL':    'trig ' + ptFmtN(payload.trigger_price,2) + ' → lim ' + ptFmtN(payload.limit_price,2),
    'SL-M':  'trig ' + ptFmtN(payload.trigger_price,2) + ' → MKT',
    'TSL':   'trail ' + ptFmtN(payload.trail_value,2) + ' pts',
    'GTT':   'trig ' + ptFmtN(payload.trigger_price,2) + ' → lim ' + ptFmtN(payload.limit_price,2)
             + ' (valid ' + payload.gtt_expiry_days + 'd)',
  }[payload.order_type] || 'MKT';
  const label = payload.symbol + ' ' + (payload.strike ? payload.strike+' '+payload.instrument_type : payload.instrument_type);
  const sideColor = payload.side === 'BUY' ? 'var(--pos,#2ecc71)' : 'var(--neg,#e74c3c)';
  const lotSize = ptGetLotSize(payload.symbol);
  const totalUnits = lotSize == null ? null : Number(payload.qty_lots) * lotSize;
  const liveLtp = typeof ptResolveLtp === 'function'
    ? ptResolveLtp(payload.symbol, payload.instrument_type, payload.expiry, Number(payload.strike))
    : null;
  const referencePrice = payload.order_type === 'LIMIT' ? Number(payload.limit_price) : Number(liveLtp);
  const estimatedValue = totalUnits != null && Number.isFinite(referencePrice) && referencePrice > 0
    ? totalUnits * referencePrice : null;
  const safeLabel = ptEscAttr(label);
  const safeExpiry = ptEscAttr(payload.expiry || 'N/A');

  const body = $i('pt-live-confirm-body');
  if(body){
    body.innerHTML =
      '<b>' + safeLabel + '</b><br>' +
      'Expiry: <b>' + safeExpiry + '</b><br>' +
      'Side: <b style="color:' + sideColor + '">' + ptEscAttr(payload.side) + '</b> &nbsp; ' +
      'Qty: <b>' + payload.qty_lots + ' lot' + (payload.qty_lots===1?'':'s') + '</b>' +
      (totalUnits == null ? '' : ' · <b>' + totalUnits + ' units</b>') + '<br>' +
      'Lot size: <b>' + (lotSize == null ? 'unresolved' : lotSize) + '</b><br>' +
      'Type: <b>' + payload.order_type + '</b> &nbsp; Price: <b>' + priceBit + '</b><br>' +
      'Estimated value: <b>' + (estimatedValue == null ? 'unavailable' : '₹' + ptFmtN(estimatedValue,2)) + '</b><br>' +
      '<span style="opacity:.7;font-size:11px;">This will place a REAL order on your AngelOne account.</span>';
  }

  const overlay = $i('pt-live-confirm-overlay');
  const yesBtn = $i('pt-live-confirm-yes');
  const noBtn = $i('pt-live-confirm-no');
  if(!overlay || !yesBtn || !noBtn){
    // Modal DOM missing somehow — fail SAFE: do not place a live order
    // without the confirmation step ever having been shown.
    ptToast('Live confirmation dialog unavailable — order NOT sent', 'err');
    if(btn && btn.setError) btn.setError('Confirm dialog unavailable');
    return false;
  }

  const cleanup = () => {
    overlay.classList.remove('open');
    yesBtn.onclick = null;
    noBtn.onclick = null;
  };

  yesBtn.onclick = () => {
    cleanup();
    payload.live = true;
    payload.confirmed = true;
    _ptSendOrderNow(payload, errEl, btn);
  };
  noBtn.onclick = () => {
    cleanup();
    ptToast('Live order cancelled — nothing sent', 'ok');
  };

  overlay.classList.add('open');
  return true; // modal shown; actual send is deferred to the confirm click
}

// ── Shared DOM hosts ─────────────────────────────────────────────────
// Mounts the pieces neither panel owns individually: the toast stack
// (#pt-toast-wrap, used by both), the quick-order popover host (opened
// from the option chain — order-entry.js builds its contents, but the
// host node + click-away/Escape dismissal are generic), and the live-order
// confirmation overlay (shown by ptDispatchOrder above regardless of which
// panel triggered it). Called once by portfolio-tracker.js's ptMountPanel()
// orchestrator, before the two panels themselves are mounted.
function ptMountSharedHosts(){
  // Toast host (for order-sent / order-failed confirmations) — CSS for
  // #pt-toast-wrap already exists in styles/paper-trading.css, it just
  // needs a DOM node.
  const toastWrap = document.createElement('div');
  toastWrap.id = 'pt-toast-wrap';
  document.body.appendChild(toastWrap);

  // Quick BUY/SELL popover host, opened by clicking an LTP cell in the
  // option chain (order-entry.js's ptOpenQuickOrder). Hidden until populated.
  const qp = document.createElement('div');
  qp.id = 'pt-quick-popover';
  qp.style.display = 'none';
  document.body.appendChild(qp);
  document.addEventListener('click', (e)=>{
    const pop = $i('pt-quick-popover');
    if(pop && pop.style.display !== 'none' && !pop.contains(e.target) && !e.target.classList.contains('pt-ltp-click')){
      pop.style.display = 'none';
    }
  });
  document.addEventListener('keydown', (e)=>{
    if(e.key === 'Escape'){ const pop=$i('pt-quick-popover'); if(pop) pop.style.display='none'; }
  });

  // Live-order confirmation modal — the last checkpoint before a real
  // order reaches the broker. Populated per-order by ptDispatchOrder()
  // above when _ptLiveMode is on; Confirm/Cancel handlers are wired fresh
  // each time it's shown (see ptDispatchOrder) rather than once here,
  // since each order needs its own closure over that specific payload/errEl.
  const liveOverlay = document.createElement('div');
  liveOverlay.id = 'pt-live-confirm-overlay';
  liveOverlay.innerHTML = `
    <div id="pt-live-confirm-box">
      <h5>⚠ Confirm LIVE Order</h5>
      <div id="pt-live-confirm-body"></div>
      <div id="pt-live-confirm-btns">
        <button id="pt-live-confirm-no">Cancel</button>
        <button id="pt-live-confirm-yes">Place Real Order</button>
      </div>
    </div>
  `;
  document.body.appendChild(liveOverlay);
  // Click on the dark backdrop (not the box itself) also cancels.
  liveOverlay.addEventListener('click', (e)=>{
    if(e.target === liveOverlay) $i('pt-live-confirm-no').click();
  });
}
