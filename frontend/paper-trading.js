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

// Symbols this UI actually offers in the dropdown (see PT_LOT_SIZES.forEach
// usage further down) — used to warm the cache on load.
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
  console.warn('[paper-trading] lot size not yet resolved for "' + symbol + '" — skipping this value rather than guessing 1. Will self-correct once /api/lot-sizes responds.');
}

// Populates PT_LOT_SIZES from the backend. Wire this to whatever endpoint
// exposes paper_trading.get_lot_size() per symbol — e.g. a REST route like
// GET /api/lot-sizes returning {"NIFTY":75,"BANKNIFTY":35,...}, or a WS
// request/response message if ws_server_live.py already has a channel for
// this. Call once on panel init, and re-call periodically (e.g. once at
// market open) since lot sizes can change on NSE's quarterly review.
async function ptRefreshLotSizes(){
  try{
    const res = await fetch('/api/lot-sizes');
    if(!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    Object.assign(PT_LOT_SIZES, data);
    ptUpdateLotSizeHint();
  }catch(e){
    console.warn('[paper-trading] lot size refresh failed, using cached/fallback values', e);
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
// treating as permanent. Brokerage defaults to 0 here since this is a
// paper-trading simulator with no real broker attached; set it if you
// want to model a specific broker's flat/percentage fee on top.
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
  brokeragePerOrder:0,          // paper-trading assumption — adjust to model a real broker
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
  return {
    netPnl, capital, marginBlocked, fund,
    lowFund: fund < PT_STARTING_CAPITAL * PT_LOW_FUND_PCT,
    isLive: false,
    fundSource: 'paper-estimate'
  };
}
window.ptComputeFundSummary = ptComputeFundSummary;

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

function ptFmtN(n, d){
  if(n===null||n===undefined||n===''||isNaN(n)) return '—';
  return Number(n).toLocaleString('en-IN', {minimumFractionDigits:d??2, maximumFractionDigits:d??2});
}

// Escapes text for safe use inside an HTML attribute (e.g. a rejection
// reason dropped into data-reason="..."). Reasons come from the backend
// and may contain quotes/HTML-ish characters we don't control.
function ptEscAttr(s){
  return String(s==null?'':s)
    .replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')
    .replace(/</g,'&lt;').replace(/>/g,'&gt;');
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

// True once the user has explicitly changed the Symbol dropdown
// themselves — before that, renderPaperTradingPanel() is free to keep
// syncing it to whatever symbol the WS is actually streaming (see the
// mount-race bugfix below: the panel mounts on DOMContentLoaded, which
// fires before connectWebSocket()'s first tick, so at mount time there is
// no live symbol to prefill from yet).
let _ptSymbolTouched = false;

function ptMountPanel(){
  if($i('pt-toggle-btn')) return; // already mounted, e.g. after bfcache-forced reload path

  // Styling for #pt-panel/#pt-quick-popover/.pt-toast/#pt-live-confirm-*/etc.
  // now lives in styles.css (was runtime-injected here via a <style> tag).

  const btn = document.createElement('button');
  btn.id = 'pt-toggle-btn';
  btn.textContent = '📊 Paper';
  btn.onclick = () => $i('pt-panel').classList.toggle('open');
  document.body.appendChild(btn);

  const panel = document.createElement('div');
  panel.id = 'pt-panel';
  panel.innerHTML = `
    <h4><span id="pt-panel-title">Paper Trading</span> <span id="pt-mode-toggle" class="pt-mode-toggle paper" onclick="ptToggleLiveMode()" title="Click to switch between Paper and Live trading">📝 PAPER</span> <span class="pt-close" onclick="$i('pt-panel').classList.remove('open')">✕</span></h4>
    <div class="pt-section">
      <div class="pt-row">
        <select id="pt-symbol"></select>
        <select id="pt-instype">
          <option value="CE">CE</option><option value="PE">PE</option>
          <option value="FUT">FUT</option><option value="INDEX">INDEX</option>
        </select>
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
      <div id="pt-lotsize-hint" style="font-size:10px;opacity:.65;margin:-4px 0 6px;">Lot size: — · Total qty: —</div>
      <div class="pt-row">
        <div class="pt-toggle-group pt-toggle-group-6" id="pt-ordtype-toggle" role="group" aria-label="Order type">
          <button type="button" class="pt-toggle-btn active" data-value="MARKET">MARKET</button>
          <button type="button" class="pt-toggle-btn" data-value="LIMIT">LIMIT</button>
          <button type="button" class="pt-toggle-btn" data-value="SL">SL</button>
          <button type="button" class="pt-toggle-btn" data-value="SL-M">SL-M</button>
          <button type="button" class="pt-toggle-btn" data-value="TSL">TSL</button>
          <button type="button" class="pt-toggle-btn" data-value="GTT">GTT</button>
        </div>
        <select id="pt-ordtype" style="display:none;">
          <option value="MARKET">MARKET</option><option value="LIMIT">LIMIT</option>
          <option value="SL">SL</option><option value="SL-M">SL-M</option>
          <option value="TSL">TSL</option><option value="GTT">GTT</option>
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
      <div id="pt-trigger-pct-hint" style="display:none;font-size:10px;opacity:.65;margin:-4px 0 6px;"></div>
      <div class="pt-row" id="pt-trail-row" style="display:none;">
        <input id="pt-trail-value" type="number" min="0.05" step="0.05" placeholder="Trail by (points)">
      </div>
      <div class="pt-row" id="pt-gtt-row" style="display:none;">
        <input id="pt-gtt-expiry" type="number" min="1" value="30" placeholder="GTT valid for (days)">
      </div>
      <div id="pt-ltp-hint" style="font-size:10px;opacity:.65;margin:-4px 0 6px;">LTP: —</div>
      <div class="pt-row">
        <button class="pt-submit" id="pt-submit-btn" style="flex:2;">Place Order</button>
        <button class="pt-submit" id="pt-add-basket-btn" style="flex:1;background:var(--bg2,#1a1a1a);color:var(--text,#eee);border:1px solid var(--border,#333);" title="Stage this leg into a basket instead of sending it now">+ Basket</button>
      </div>
      <div id="pt-err"></div>
      <div id="pt-basket-wrap" style="display:none;margin-top:8px;">
        <div style="font-size:11px;font-weight:800;opacity:.8;margin-bottom:4px;">Basket (<span id="pt-basket-count">0</span> legs)</div>
        <div id="pt-basket-list"></div>
        <div class="pt-row" style="margin-top:6px;">
          <button class="pt-submit" id="pt-place-basket-btn" style="flex:2;">Place Basket</button>
          <button class="pt-submit" id="pt-clear-basket-btn" style="flex:1;background:var(--bg2,#1a1a1a);color:var(--red,#e74c3c);border:1px solid var(--border,#333);">Clear</button>
        </div>
      </div>
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
        <span title="₹1,00,000 starting paper capital ± net P&amp;L, minus margin currently used above.">Fund (available)</span><span id="pt-fund">—</span>
      </div>
      <div id="pt-fund-warn" style="display:none;font-size:10px;color:var(--red,#e74c3c);margin-top:4px;">⚠ Fund running low — consider squaring off open positions.</div>
    </div>
    <div class="pt-section">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
        <span style="font-size:10px;font-weight:700;color:var(--muted,#888);text-transform:uppercase;letter-spacing:.05em;">Positions</span>
        <button id="pt-squareoff-all-btn" onclick="ptSquareOffAll()" title="Send an opposite-side MARKET order to flatten every open position"
          style="font-size:10px;font-weight:700;padding:3px 8px;border:none;border-radius:4px;background:var(--red,#e74c3c);color:#fff;cursor:pointer;">Square Off All</button>
      </div>
      <table id="pt-positions-table"><thead><tr>
        <th>Sym</th><th>Expiry</th><th>Strike/Ty</th><th>Net</th><th>Avg</th><th>LTP</th><th>uPnL</th><th></th>
      </tr></thead><tbody></tbody></table>
    </div>
    <div class="pt-section">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
        <span style="font-size:10px;font-weight:700;color:var(--muted,#888);text-transform:uppercase;letter-spacing:.05em;">Order / Trade Log</span>
        <button id="pt-reset-log-btn" onclick="ptResetOrderLog()" title="Clear the order/trade log shown below (does not affect open positions or P&amp;L)"
          style="font-size:10px;font-weight:700;padding:3px 8px;border:none;border-radius:4px;background:var(--muted,#555);color:#fff;cursor:pointer;">Reset</button>
      </div>
      <table id="pt-orders-table"><thead><tr>
        <th>Sym</th><th>Expiry</th><th>Side</th><th>Qty</th><th>Type</th><th>Price</th><th>Charges</th><th>Status</th><th>Time</th>
      </tr></thead><tbody></tbody></table>
    </div>
  `;
  document.body.appendChild(panel);

  // Tap-to-reveal for rejected orders' reason (see .pt-status-tap CSS note
  // above) — delegated once on the table body since rows are rebuilt on
  // every re-render.
  panel.addEventListener('click', (e)=>{
    const td = e.target.closest('.pt-status-tap');
    if(td) ptToast('Rejected — ' + (td.dataset.reason || 'no reason provided'), 'err');
  });

  // Toast host (for order-sent / order-failed confirmations) — CSS for
  // #pt-toast-wrap already exists above, it just never had a DOM node.
  const toastWrap = document.createElement('div');
  toastWrap.id = 'pt-toast-wrap';
  document.body.appendChild(toastWrap);

  // Quick BUY/SELL popover host, opened by clicking an LTP cell in the
  // option chain (ptOpenQuickOrder). Hidden until populated.
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
  // when _ptLiveMode is on; Confirm/Cancel handlers are wired fresh each
  // time it's shown (see ptDispatchOrder) rather than once here, since
  // each order needs its own closure over that specific payload/errEl.
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

  console.log('[paper-trading] panel mounted:', document.body.contains(btn), document.body.contains(panel));

  PT_KNOWN_SYMBOLS.forEach(sym=>{
    const o = document.createElement('option'); o.value = sym; o.textContent = sym;
    $i('pt-symbol').appendChild(o);
  });

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
  ptWireToggleGroup('pt-side-toggle', 'pt-side');
  ptWireToggleGroup('pt-ordtype-toggle', 'pt-ordtype');
  ptWireToggleGroup('pt-trigger-mode-toggle', 'pt-trigger-mode');
  $i('pt-trigger-mode').onchange = ptUpdateTriggerModeUi;
  $i('pt-trigger-price').addEventListener('input', ptUpdateTriggerPctHint);
  $i('pt-add-basket-btn').onclick = ptAddToBasket;
  $i('pt-place-basket-btn').onclick = ptPlaceBasket;
  $i('pt-clear-basket-btn').onclick = ()=>{ _ptBasket = []; ptRenderBasket(); };
  $i('pt-instype').onchange = ptRefreshExpiryStrikeOptions;
  $i('pt-expiry').onchange  = ptRefreshStrikeOptions;
  $i('pt-strike').onchange  = ptUpdateLtpHint;
  $i('pt-symbol').onchange  = ()=>{ _ptSymbolTouched = true; ptRefreshExpiryStrikeOptions(); ptUpdateLotSizeHint(); };
  $i('pt-qty').addEventListener('input', ptUpdateLotSizeHint);

  $i('pt-submit-btn').onclick = ptSubmitOrder;

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
  ptRenderBasket();
}

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

// Shared by ptSubmitOrder (send immediately) and ptAddToBasket (stage a
// leg without sending it yet) — reads the form once, validates it against
// whatever the selected order type actually requires, and returns either
// {order} or {error}. Keeping this in one place means SL/SL-M/TSL/GTT
// validation rules only need to be right in one spot, not duplicated
// between "place now" and "add to basket".
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
  if((order_type === 'LIMIT' || order_type === 'SL' || order_type === 'GTT')
     && (limit_price === null || isNaN(limit_price))){
    return { error: `Limit price required for ${order_type} orders` };
  }
  if((order_type === 'SL' || order_type === 'SL-M' || order_type === 'GTT')
     && (trigger_price === null || isNaN(trigger_price))){
    const pctModeStuck = $i('pt-trigger-mode').value === 'pct' && $i('pt-trigger-price').value !== '';
    return { error: pctModeStuck
      ? 'No live price yet to resolve the % trigger — wait for a tick or switch to ₹.'
      : `Trigger price required for ${order_type} orders` };
  }
  if(order_type === 'TSL' && (trail_value === null || isNaN(trail_value) || trail_value <= 0)){
    return { error: 'Trail value (points) required for TSL orders' };
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
  // Field names below are what the backend needs to read for each type
  // (send whichever of trigger_price/limit_price/trail_value/gtt_expiry_days
  // apply — the rest come through as null and can be ignored server-side):
  //   LIMIT : limit_price
  //   SL    : trigger_price + limit_price  (rests as a LIMIT once triggered)
  //   SL-M  : trigger_price only            (fills at MARKET once triggered)
  //   TSL   : trail_value                   (points; recompute the live
  //           trigger server-side as LTP moves in the position's favor —
  //           frontend only ever sends the trail distance, never a fixed
  //           trigger, since the whole point of TSL is the trigger moves)
  //   GTT   : trigger_price + limit_price + gtt_expiry_days (days until
  //           the GTT auto-cancels server-side if never triggered)
  const order = { symbol, instrument_type, expiry, strike, side, qty_lots, order_type,
                   limit_price, trigger_price, trail_value, gtt_expiry_days };
  return { order };
}

function ptSubmitOrder(){
  const errEl = $i('pt-err');
  errEl.style.color = 'var(--red,#e74c3c)';
  errEl.textContent = '';
  const { order, error } = ptGatherOrderFromForm();
  if(error){ errEl.textContent = error; return; }
  ptDispatchOrder(order, errEl);
}

// ── Basket orders ─────────────────────────────────────────────────────
// A basket is just several legs staged client-side, then sent together in
// one 'place_basket_order' WS message as {legs:[...]} so the backend can
// treat them as one atomic submission (e.g. an Iron Condor's 4 legs going
// in together) instead of 4 separate 'place_order' round-trips.
let _ptBasket = [];

function ptAddToBasket(){
  const errEl = $i('pt-err');
  errEl.style.color = 'var(--red,#e74c3c)';
  errEl.textContent = '';
  const { order, error } = ptGatherOrderFromForm();
  if(error){ errEl.textContent = error; return; }
  _ptBasket.push(order);
  ptRenderBasket();
  errEl.style.color = 'var(--green,#2ecc71)';
  errEl.textContent = 'Added to basket';
  setTimeout(()=>{ if(errEl.textContent==='Added to basket') errEl.textContent=''; }, 1500);
}

function ptRenderBasket(){
  const wrap = $i('pt-basket-wrap');
  const list = $i('pt-basket-list');
  const count = $i('pt-basket-count');
  if(!wrap || !list || !count) return;
  wrap.style.display = _ptBasket.length ? '' : 'none';
  count.textContent = _ptBasket.length;
  list.innerHTML = _ptBasket.map((o, i)=>{
    const label = o.symbol + ' ' + (o.strike ? o.strike+' '+o.instrument_type : o.instrument_type);
    const sideCls = o.side === 'BUY' ? 'pt-side-buy' : 'pt-side-sell';
    return `<div class="pt-basket-leg">
      <span><span class="pt-side-badge ${sideCls}">${o.side}</span> ${label} × ${o.qty_lots} (${o.order_type})</span>
      <span class="pt-basket-leg-remove" data-idx="${i}">✕</span>
    </div>`;
  }).join('');
  list.querySelectorAll('.pt-basket-leg-remove').forEach(el=>{
    el.onclick = ()=>{ _ptBasket.splice(parseInt(el.dataset.idx, 10), 1); ptRenderBasket(); };
  });
}

function ptPlaceBasket(){
  const errEl = $i('pt-err');
  errEl.style.color = 'var(--red,#e74c3c)';
  if(!_ptBasket.length){ errEl.textContent = 'Basket is empty'; return; }
  const ok = sendWsMessage('place_basket_order', { legs: _ptBasket });
  if(ok){
    ptToast('Basket sent — ' + _ptBasket.length + ' leg' + (_ptBasket.length===1?'':'s'), 'ok');
    errEl.style.color = 'var(--green,#2ecc71)';
    errEl.textContent = 'Basket sent';
    setTimeout(()=>{ if(errEl.textContent==='Basket sent') errEl.textContent=''; }, 2000);
    _ptBasket = [];
    ptRenderBasket();
    const panel = $i('pt-panel');
    if(panel) panel.classList.add('open');
  } else {
    ptToast('Basket failed to send (WS not connected)', 'err');
    errEl.textContent = 'WS not connected — basket not sent';
  }
}

// Shared by both the panel's "Place Order" button and the option-chain
// quick BUY/SELL popover. Sends the order, shows a toast confirmation
// immediately (previously the ONLY feedback was a 2s-then-vanish
// "Order sent" string on the panel form, and if the panel was closed —
// e.g. ordering from the quick popover — there was no confirmation at
// all), and logs a locally-tracked "pending" row into the orders table
// so something visible shows up right away even before the backend's
// next `orders` WS message round-trips back.
// Global paper/live mode switch. Deliberately NOT persisted to
// localStorage/sessionStorage — every fresh page load starts back in
// Paper mode. A page reload should never silently carry live mode forward;
// re-enabling it is a deliberate action the person has to take again each
// session.
let _ptLiveMode = false;

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
  const panel = $i('pt-panel');
  const floatBtn = $i('pt-toggle-btn');
  if(pill){
    pill.textContent = _ptLiveMode ? '🔴 LIVE' : '📝 PAPER';
    pill.classList.toggle('live', _ptLiveMode);
    pill.classList.toggle('paper', !_ptLiveMode);
  }
  const title = $i('pt-panel-title');
  if(title) title.textContent = _ptLiveMode ? 'Live Trading' : 'Paper Trading';
  if(panel) panel.classList.toggle('live-mode', _ptLiveMode);
  if(floatBtn) floatBtn.textContent = _ptLiveMode ? '🔴 Live' : '📊 Paper';
  ptToast(_ptLiveMode ? 'LIVE trading mode enabled' : 'Back to Paper trading mode', _ptLiveMode ? 'err' : 'ok');
  // Directs the socket to start/stop real funds polling, the same way
  // switching the top-bar symbol dropdown directs it to switch feeds —
  // no server restart involved. This ONLY controls funds polling; it does
  // NOT enable real order placement, which stays gated server-side by
  // LIVE_TRADING_ENABLED (a deliberate restart-only decision — see
  // ws_server_live.py) regardless of what this sends.
  sendWsMessage('toggle_live_mode', { enabled: _ptLiveMode });
}
window.ptToggleLiveMode = ptToggleLiveMode;

let _ptPending = []; // {id, symbol, side, qty_lots, order_type, limit_price, strike, instrument_type, ts, status}

// A pending row and its eventual backend confirmation don't share an id
// at send time (the id only exists once the backend assigns one), so
// match on the order's identifying fields instead: same instrument, same
// side/qty/type, confirmed at-or-after the moment it was sent (with a
// couple seconds' grace for clock skew between browser and server).
function ptFindMatchingConfirmedOrder(pending, orders){
  return (orders || []).some(o=>{
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

function _ptSendOrderNow(payload, errEl){
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
    const pending = Object.assign({}, payload, {id:'pend_'+Date.now()+'_'+Math.random().toString(36).slice(2), status:'SENT', ts:Date.now()});
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
      errEl.style.color = 'var(--green,#2ecc71)';
      errEl.textContent = 'Order sent';
      setTimeout(()=>{ if(errEl.textContent==='Order sent') errEl.textContent=''; }, 2000);
    }
    // Make sure the confirmation + orders table are actually visible —
    // this was the core of "no trade/order information after order
    // sent": ordering from the option-chain popover left the panel
    // closed the whole time.
    const panel = $i('pt-panel');
    if(panel) panel.classList.add('open');
    if(AppState.wsState) renderPaperTradingPanel(AppState.wsState);
  } else {
    ptToast(label + ' — failed to send (WS not connected)', 'err');
    if(errEl) errEl.textContent = 'WS not connected — order not sent';
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
function ptDispatchOrder(payload, errEl){
  if(!_ptLiveMode){
    return _ptSendOrderNow(payload, errEl);
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
  const sideColor = payload.side === 'BUY' ? 'var(--green,#2ecc71)' : 'var(--red,#e74c3c)';

  const body = $i('pt-live-confirm-body');
  if(body){
    body.innerHTML =
      '<b>' + label + '</b><br>' +
      'Side: <b style="color:' + sideColor + '">' + payload.side + '</b> &nbsp; ' +
      'Qty: <b>' + payload.qty_lots + ' lot' + (payload.qty_lots===1?'':'s') + '</b><br>' +
      'Type: <b>' + payload.order_type + '</b> &nbsp; Price: <b>' + priceBit + '</b><br>' +
      '<span style="opacity:.7;font-size:11px;">This will place a REAL order on your AngelOne account.</span>';
  }

  const overlay = $i('pt-live-confirm-overlay');
  const yesBtn = $i('pt-live-confirm-yes');
  const noBtn = $i('pt-live-confirm-no');
  if(!overlay || !yesBtn || !noBtn){
    // Modal DOM missing somehow — fail SAFE: do not place a live order
    // without the confirmation step ever having been shown.
    ptToast('Live confirmation dialog unavailable — order NOT sent', 'err');
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
    _ptSendOrderNow(payload, errEl);
  };
  noBtn.onclick = () => {
    cleanup();
    ptToast('Live order cancelled — nothing sent', 'ok');
  };

  overlay.classList.add('open');
  return true; // modal shown; actual send is deferred to the confirm click
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

// Opens a small BUY/SELL popover anchored to the LTP cell that was
// clicked in the option chain, so an order can be placed against that
// exact strike without ever touching the main panel's dropdowns.
function ptOpenQuickOrder(evt, strike, instrument_type, ltp){
  evt.stopPropagation();
  const pop = $i('pt-quick-popover');
  if(!pop || !AppState.wsState) return;
  const symbol = AppState.wsState.symbol || '';
  const expiry = AppState.wsState._activeExpiry || _selectedExpiry || AppState.wsState.expiry || '';
  pop.innerHTML = `
    <div class="pt-qp-hdr"><span>${symbol} ${fmtI(strike)} ${instrument_type}</span><span class="pt-qp-close" onclick="$i('pt-quick-popover').style.display='none'">✕</span></div>
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
    console.error('[paper-trading] ptExecuteLeg failed', {symbol, expiry, strike, instrument_type, side, lots, ltp}, e);
    ptToast((side||'') + ' ' + (strike||'') + ' ' + (instrument_type||'') + ' — leg failed to send, see console', 'err');
  }
}
window.ptExecuteLeg = ptExecuteLeg;

function ptExecuteStrategy(){
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
      console.warn('[decision-box] skipped leg — no live LTP:', l);
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

// Flattens one open position with a single opposite-side MARKET order —
// e.g. net_qty_lots=+3 (long) sends a SELL 3, net_qty_lots=-2 (short)
// sends a BUY 2. Routes through ptDispatchOrder() like every other order
// path here, so it shows up as a normal FILLED order in the orders table
// and the position disappears from the positions table once the next
// portfolio broadcast lands (place_order -> _apply_fill_to_position nets
// it to zero, and get_positions() only returns net_qty_lots != 0 rows).
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

// Backend-agnostic "live portfolio" fix: paper_trading.py's last_price /
// unrealized_pnl on each position only reflect whatever LTP it had at
// the time it last recomputed (typically on order/fill events). Rather
// than wait for a backend change, re-price every open CE/PE/FUT/INDEX
// position here against whatever chain/spot data this tick's AppState.wsState
// already carries, so the portfolio panel tracks the live market tick
// by tick instead of freezing between orders.
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
        console.warn('[paper-trading] position symbol "'+p.symbol+'" did not match active tick symbol "'+d.symbol+'" — live reprice skipped for this position. If these are supposed to be the same symbol, check for case/whitespace differences at the source.');
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

// BUGFIX: the symbol/expiry/strike/LTP sync below used to live AFTER the
// `if(!wsState.portfolio) return;` guard further down, which meant NONE
// of it ever ran until the backend started sending {type:"portfolio",...}
// messages. That message type needs separate wiring into
// ws_server_live.py (see the note above sendWsMessage()) and may not be
// hooked up yet — but the option chain (which is what actually drives
// the expiry/strike dropdowns) arrives via a completely different,
// already-working WS message stream. So this sync must not be blocked
// on `portfolio` existing at all — only the P&L/positions/orders
// rendering below genuinely needs it. Kept separate from the P&L calc/
// render split below since it's neither: it's DOM form state syncing
// against the live tick, not a derived number and not a table paint.
function ptSyncFormFromWsState(wsState){
  // BUGFIX: pt-symbol is prefilled from AppState.wsState.symbol at mount time, but
  // ptMountPanel() runs on DOMContentLoaded — before connectWebSocket()'s
  // first tick — so AppState.wsState.symbol is usually still unknown then and the
  // dropdown silently falls back to whatever's first in PT_LOT_SIZES
  // (NIFTY). Nothing ever re-synced it once the real symbol arrived, so if
  // the backend was actually streaming e.g. BANKNIFTY the form stayed
  // pinned to NIFTY forever and expiry/strike lookups never matched.
  const symSel = $i('pt-symbol');
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

  // Fund / available margin — see ptComputeFundSummary() above for the
  // capital/margin model. Uses the same wsState so this stays in lockstep
  // with Realized/Unrealized/Total rather than recomputing pf a second time.
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
      // comment) — say so plainly rather than showing a paper number that
      // would look like a real balance.
      setHtmlIfChanged($i('pt-margin-used'), '<span title="Live account margin isn\'t wired up yet — see paper-trading.js">not available (live)</span>');
      setHtmlIfChanged($i('pt-fund'), '<span title="Live account funds aren\'t wired up yet — see paper-trading.js">not available (live)</span>');
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
    const exitBtn = '<span onclick="ptSquareOffPosition(\''+p.symbol+'\',\''+(p.expiry||'')+'\','
      + (p.strike==null?'null':p.strike) + ',\''+p.instrument_type+'\','+p.net_qty_lots+')" '
      + 'title="Exit this position (opposite-side MARKET order)" '
      + 'style="cursor:pointer;font-size:9px;font-weight:800;padding:1px 6px;border-radius:4px;'
      + 'background:var(--red,#e74c3c);color:#fff;">✕</span>';
    return '<tr><td>'+p.symbol+'</td><td title="'+(p.expiry||'')+'">'+expCell+'</td><td>'+label+'</td><td>'+p.net_qty_lots+'</td>'
      + '<td>'+ptFmtN(p.avg_price)+'</td><td>'+ptFmtN(p.last_price)+(p._live?' <span title="live" style="color:var(--green,#2ecc71);">●</span>':'')+'</td>'
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
    
    let statusTd = '<td>'+o.status+'</td>';
    if(isRejected){
      statusTd = '<td class="pt-neg pt-status-tap" data-reason="'+ptEscAttr(statusReason || 'No reason provided by engine')+'" title="Tap for reason">'+o.status+'</td>';
    } else if(isPending){
      const cancelBtn = '<span onclick="ptCancelOrder(\''+o.id+'\')" title="Cancel this pending order" '
        + 'style="cursor:pointer;margin-left:6px;font-size:9px;font-weight:800;padding:1px 4px;border-radius:3px;'
        + 'background:var(--red,#e74c3c);color:#fff;">✕</span>';
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

// ── Orchestrator ── unchanged entry point / call signature, now just
// wires: mount -> form sync (always) -> guard -> compute -> render x3.
function renderPaperTradingPanel(wsState){
  if(!$i('pt-panel')) ptMountPanel();
  if(!wsState) return;

  // Must not be blocked on `portfolio` existing — see ptSyncFormFromWsState.
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
