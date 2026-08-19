// Market context: index ticker, symbol/source selection and expiry state.
// Loaded after chain-helpers.js and before the chain view/controller files.

function renderIndexTicker(d) {
  if (!d) d = {};

  // The 'indices' array comes from your new unified stream in mTerminals_json.py
  const indices = d.allIndices || [];
  const active = d.symbol || 'NIFTY';

  // FUT pill in the first slot (replaces the old VIX pill): near-month
  // futures symbol + price, day change / % change (smallest tier), basis,
  // and the NEAR/NEXT/FAR contract dropdown — the whole futures-reference
  // widget, moved in here from the expiry strip (same ids the top-bar
  // patch in chain-dashboard-renderer.js updates: topbar-future,
  // topbar-basis, futuresExpirySelect).
  const futPrice = Number(d.future) || 0;
  const futChg = Number(d.futureChange) || 0;
  const futPct = Number(d.futureChgPct) || 0;
  const futUp = futPct >= 0;
  const futExpiry = d.futuresExpiry || 'NEAR';
  const futOptions = ['NEAR', 'NEXT', 'FAR']
    .map(v => `<option value="${v}"${futExpiry === v ? ' selected' : ''}>${v}</option>`)
    .join('');
  const futChgHtml = (d.futureChgPct !== undefined && (futChg || futPct))
    ? `<span class="idx-pill-chg ${futUp ? 'up' : 'down'}">${futUp ? '▲' : '▼'} ${Math.abs(futChg).toFixed(2)} ${Math.abs(futPct).toFixed(2)}%</span>`
    : '';
  const futBasisHtml = `<small id="topbar-basis" class="idx-pill-basis">${Number(d.basis) >= 0 ? '+' : ''}${fmtN(Number(d.basis) || 0, 1)}</small>`;
  const futPill = `<div class="idx-pill idx-pill-fut" data-index-symbol="FUT" title="Near-month futures — confirmation only, does not change Greeks or confidence">
    <span class="idx-pill-fut-row">
      <span class="idx-pill-sym">FUT</span>
      <span class="idx-pill-val" id="topbar-future">${futPrice > 0 ? fmtI(futPrice) : '—'}</span>
      <select id="futuresExpirySelect" class="price-source-select futures-expiry-select" title="Futures reference contract" onchange="onFuturesExpiryPicked(this.value)">${futOptions}</select>
    </span>
    <span class="idx-pill-fut-row idx-pill-fut-meta">
      ${futChgHtml}
      ${futBasisHtml}
    </span>
  </div>`;

  // Map display names to backend symbols (matches market_api.py INDEX_RENAME)
  // Backend now sends renamed symbols (NIFTY, BANKNIFTY) directly
  const symbolMap = {
    'NIFTY': 'NIFTY',
    'NIFTY BANKNIFTY': 'BANKNIFTY',
    'FINNIFTY': 'FINNIFTY',
    'MIDCPNIFTY': 'MIDCPNIFTY',
    'SENSEX': 'SENSEX'
  };

  // Map the new unified index list directly to pills
  const pills = indices
    .filter(idx => (idx.BackendSymbol || idx.Symbol) !== active) // Match your rename_index mapping
    .map(idx => {
      const pChange = parseFloat(idx["% Change"]) || 0;
      const up = pChange >= 0;
      const backendSymbol = idx.BackendSymbol || idx.Symbol;
      const displayName = idx.Symbol;

      return `<button type="button" class="idx-pill" data-index-symbol="${backendSymbol}" onclick="switchActiveIndex('${backendSymbol}')" title="Switch to ${displayName}">
        <span class="idx-pill-sym">${displayName}</span>
        <span class="idx-pill-val">${fmtI(idx["Last Price"])}</span>
        <span class="idx-pill-chg ${up ? 'up' : 'down'}">${up ? '▲' : '▼'}${Math.abs(pChange).toFixed(2)}%</span>
      </button>`;
    }).join('');

  return `<div class="index-ticker" id="index-ticker-bar">${futPill}${pills}</div>`;
}

// Patch the frequently-changing quote values without replacing ticker buttons.
// A structural rebuild is reserved for an actual change to the available index
// set; routine quote ticks retain the same DOM nodes and cannot cancel a click.
function patchIndexTicker(d) {
  const ticker = document.getElementById('index-ticker-bar');
  if (!ticker) return;
  const active = d.symbol || 'NIFTY';
  const indices = (d.allIndices || []).filter((idx) => (idx.BackendSymbol || idx.Symbol) !== active);
  const expected = ['FUT'].concat(indices.map((idx) => idx.BackendSymbol || idx.Symbol));
  const existing = Array.from(ticker.querySelectorAll('[data-index-symbol]'))
    .map((el) => el.dataset.indexSymbol);

  if (expected.join('|') !== existing.join('|')) {
    const fresh = renderIndexTicker(d);
    const start = fresh.indexOf('>') + 1;
    ticker.innerHTML = fresh.slice(start, fresh.lastIndexOf('</div>'));
    return;
  }

  const fut = ticker.querySelector('[data-index-symbol="FUT"]');
  if (fut) {
    const value = fut.querySelector('.idx-pill-val');
    const change = fut.querySelector('.idx-pill-chg');
    const basis = fut.querySelector('.idx-pill-basis');
    const futPrice = Number(d.future) || 0;
    const futChg = Number(d.futureChange) || 0;
    const futPct = Number(d.futureChgPct) || 0;
    if (value) value.textContent = futPrice > 0 ? fmtI(futPrice) : '—';
    if (basis) basis.textContent = `Basis ${Number(d.basis) >= 0 ? '+' : ''}${fmtN(Number(d.basis) || 0, 1)}`;
    if (change && d.futureChgPct !== undefined && (futChg || futPct)) {
      change.className = 'idx-pill-chg ' + (futPct >= 0 ? 'up' : 'down');
      change.textContent = `${futPct >= 0 ? '▲' : '▼'} ${Math.abs(futChg).toFixed(2)} ${Math.abs(futPct).toFixed(2)}%`;
    }
  }

  indices.forEach((idx) => {
    const symbol = idx.BackendSymbol || idx.Symbol;
    const pill = ticker.querySelector(`[data-index-symbol="${symbol}"]`);
    if (!pill) return;
    const value = pill.querySelector('.idx-pill-val');
    const change = pill.querySelector('.idx-pill-chg');
    const pct = parseFloat(idx['% Change']) || 0;
    if (value) value.textContent = fmtI(idx['Last Price']);
    if (change) {
      change.className = 'idx-pill-chg ' + (pct >= 0 ? 'up' : 'down');
      change.textContent = `${pct >= 0 ? '▲' : '▼'}${Math.abs(pct).toFixed(2)}%`;
    }
  });
}
// ── EXPIRY SELECT RE-PARENTING ──
// #expirySelect lives once in the static DOM (see #expiry-select-holder in
// DashboardPro.html) and is never rebuilt from an HTML string — every
// top-bar redraw (full rebuild or the lighter per-tick patch) creates a
// fresh #expiry-slot placeholder (its own dedicated pill, separate from
// DTE), and this just moves the *same* <select> node into it.
//
// BUG THIS FIXES: looking the select up via document.getElementById on
// every call breaks after the first move. Once the select is appended
// into the top-bar's #expiry-slot, the *next* outerHTML replacement of
// #sec-topbar (patchTopBarAndDecision / renderDashboard) destroys that
// whole subtree — including the slot the select was sitting in — which
// detaches the select from the live document entirely. A detached node is
// invisible to getElementById, so every call after the first silently
// found nothing and the dropdown vanished for good. Caching the node
// reference once (the first time it's found) means we keep working with
// the actual same object even after it's been detached, and can always
// re-attach it into whatever fresh #expiry-slot shows up next.
let _expirySelectNode = null;
function getExpirySelectNode(){
  if(!_expirySelectNode) _expirySelectNode = document.getElementById('expirySelect');
  return _expirySelectNode;
}
function moveExpirySelectIntoTopBar(){
  const sel = getExpirySelectNode();
  const slot = document.getElementById('expiry-slot');
  if(sel && slot && sel.parentNode !== slot) slot.appendChild(sel);
}
window.moveExpirySelectIntoTopBar = moveExpirySelectIntoTopBar;

// Click handler for the pills. Switching the active index means
// reconnecting to that symbol's engine — how that's routed depends on the
// backend (a `?symbol=` query param the server reads, a distinct port per
// symbol via ws_server_live.py --symbol, etc). Define
// window.onIndexSwitchRequested(sym) before this script runs to wire the
// real routing; absent that, this falls back to a `?symbol=` query param
// on the current WS URL as the simplest single-port convention.
// ── TOP-BAR SYMBOL PICKER ──
// Seed/fallback list shown until /api/symbols resolves (see fetchSymbolList()
// in the DOMContentLoaded block below) — kept as a small known-good set in
// case that fetch is slow or fails, not a whitelist. Once /api/symbols
// returns, its contents (every OPTIDX/OPTSTK `name` in the ScripMaster —
// same primary key find_option_token()/list_expiries() key off) replace
// these in place. Mutated via length=0+push rather than reassigned, so
// chain-views.js's reference to this same array stays live.
const COMMON_SYMBOLS = ['NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY','SENSEX','BANKEX'];

// Fetches the full underlying list from the backend (backed by
// smartapi_client.list_underlyings()) and swaps it into COMMON_SYMBOLS in
// place. Fire-and-forget — called once from the DOMContentLoaded bootstrap;
// if it fails or is slow, the picker just keeps showing the seed list above
// until the next render happens to catch the updated array.
async function fetchSymbolList(){
  try{
    const res = await fetch(Config.api.symbols);
    if(!res.ok) return;
    const list = await res.json();
    if(Array.isArray(list) && list.length){
      COMMON_SYMBOLS.length = 0;
      COMMON_SYMBOLS.push(...list);
    }
  }catch(e){
    Logger.warn('symbols', '/api/symbols fetch failed, keeping seed list', e);
  }
}
window.fetchSymbolList = fetchSymbolList;

// Called by the top-bar <select onchange>. "Other…" prompts for a free-
// text symbol (individual stocks, etc.) instead of switching straight
// away — picking it directly as a value would just try to load a symbol
// literally named "__other__".
function onSymbolPicked(val){
  if(val === '__other__'){
    const sym = prompt('Symbol to switch to (e.g. RELIANCE):');
    if(sym) switchActiveIndex(sym.trim().toUpperCase());
    return;
  }
  switchActiveIndex(val);
}
window.onSymbolPicked = onSymbolPicked;

// Inside dashboard.js, within the DataService or global scope:
function switchActiveIndex(sym) {
  if (!sym) return;
  // Rebuild the WS URL preserving any existing query params (dataSource,
  // futuresExpiry, ...) so a symbol switch doesn't silently drop them —
  // same pattern as setFuturesExpiry()/applyExpirySelection().
  const [base, query] = (_wsUrl || '').split('?');
  const params = new URLSearchParams(query || '');
  // URLSearchParams percent-encodes values itself — passing sym through
  // encodeURIComponent() first double-encodes it ("ZYDUS LIFESCIENCES LTD"
  // became "ZYDUS%20LIFESCIENCES%20LTD" on the backend, which then failed
  // every expiry/chain lookup). Set the raw value.
  params.set('symbol', sym);
  connectWebSocket(`${base}?${params.toString()}`);
  // Phase 5 (event-bus.js): announce the switch on the shared bus. Purely
  // additive — connectWebSocket() above is still the only thing that
  // actually performs the switch; this just gives other modules a way to
  // react to it later without switchActiveIndex needing to know who.
  if (window.eventBus) window.eventBus.emit('symbol:change', { symbol: sym });
}
window.switchActiveIndex = switchActiveIndex;

// DATA SOURCE dropdown — runtime market-data provider switch
// (ANGEL ONE/UPSTOX/SHOONYA/ZERODHA/ICICI DIRECT/KOTAK NEO/NSE/BSE).
// Mirrors setFuturesExpiry():
// preserves the existing WS query params (symbol, expiry, futuresExpiry)
// and just sets dataSource=... then reconnects. The backend applies it
// process-wide via ws_handler() -> switch_data_source() on the very next
// engine_loop tick — no server restart, all connected clients switch
// together (see switch_data_source()'s docstring for the full sequence).
function switchDataSource(ds) {
  if (!ds) return;
  const [base, query] = (_wsUrl || '').split('?');
  const params = new URLSearchParams(query || '');
  params.set('dataSource', ds.toUpperCase());
  connectWebSocket(`${base}?${params.toString()}`);
  if (window.eventBus) window.eventBus.emit('dataSource:change', { dataSource: ds.toUpperCase() });
}
window.switchDataSource = switchDataSource;

function onDataSourcePicked(val){
  switchDataSource(val);
}
window.onDataSourcePicked = onDataSourcePicked;

window.renderIndexTicker = renderIndexTicker;
window.patchIndexTicker = patchIndexTicker;

// deepMerge() and applyDelta() live in market-store.js, used only by
// MarketStore.ingest(). See that file for both implementations.

// Called for every inbound WS message.
// msg = { type: "full" | "spot" | "oi" | "greeks" | "alerts" | "iv" | "decision", payload: {...} }
// "full" replaces the whole state; any other type is merged into the
// matching slice of state, then the dashboard is re-rendered from the
// merged state. renderDashboard() is a pure function of state -> DOM,
// so this produces correct "only the affected component visibly
// changes" behavior without needing separate per-widget DOM patchers.

// ── MOJIBAKE REPAIR ──
// If the backend ever double-encodes text (e.g. a UTF-8 string read/written
// as Windows-1252 somewhere upstream), special characters like ₹, —, or ×
// show up as garbled sequences such as "â‚¹", "â€”", "Ã—". This detects that
// specific, well-known corruption pattern and reverses it. It's a no-op
// (returns the original string untouched) for anything that isn't actually
// mojibake, so it's safe to run on every string from the feed.
// Windows-1252 remaps bytes 0x80-0x9F to non-Latin-1 codepoints (€, —, smart
// quotes, etc.) — that 0x80-0x9F range is exactly where ₹/—/× land, so a
// plain "codepoint & 0xFF" byte reconstruction silently mangles them. This
// table maps those codepoints back to their original byte value.
// Futures-reference selector. It changes only the displayed/confirming
// contract and never the EQ analytical reference.
function setFuturesExpiry(exp) {
  if (exp !== 'NEAR' && exp !== 'NEXT' && exp !== 'FAR') return;
  const [base, query] = (_wsUrl || '').split('?');
  const params = new URLSearchParams(query || '');
  params.delete('priceSource');
  params.set('futuresExpiry', exp);
  connectWebSocket(`${base}?${params.toString()}`);
  if (window.eventBus) window.eventBus.emit('futuresExpiry:change', { exp });
}
window.setFuturesExpiry = setFuturesExpiry;

// Called by the top-bar <select onchange> next to the price-source picker.
function onFuturesExpiryPicked(val){
  setFuturesExpiry(val);
}
window.onFuturesExpiryPicked = onFuturesExpiryPicked;
function parseExpiryDate(str){
  if(!str) return Infinity;
  const m = /^(\d{1,2})-([A-Za-z]{3})-(\d{4})$/.exec(String(str).trim());
  if(m){
    const mon = _EXPIRY_MONTHS[m[2].toUpperCase()];
    if(mon != null) return new Date(+m[3], mon, +m[1]).getTime();
  }
  const parsed = Date.parse(str);
  return isNaN(parsed) ? Infinity : parsed;
}
// Returns a NEW array in true calendar order — never mutates the input,
// since callers (renderExpiryOptions' dashboard-key check, option-chain.js's
// dataset-key check) compare the array's join() to detect changes and rely
// on it being the same reference/order the payload sent unless explicitly
// resorted here.
function sortExpiryDates(dates){
  if(!Array.isArray(dates)) return dates;
  return dates.slice().sort((a,b)=>parseExpiryDate(a)-parseExpiryDate(b));
}
window.parseExpiryDate = parseExpiryDate;
window.sortExpiryDates = sortExpiryDates;

function activeAtm(d){
  if(!d) return 0;
  const chain=(d.chain)||[];
  if(d.atm && chain.some(r=>r.strike===d.atm)) return d.atm;
  const atmRow=chain.find(r=>r.atm);
  if(atmRow) return atmRow.strike;
  const rowWithAtmStrike=chain.find(r=>r.atmStrike && chain.some(x=>x.strike===r.atmStrike));
  if(rowWithAtmStrike) return rowWithAtmStrike.atmStrike;
  const spot=parseFloat(d.spot)||0;
  if(spot && chain.length) return chain.reduce((best,r)=>Math.abs(r.strike-spot)<Math.abs(best.strike-spot)?r:best,chain[0]).strike;
  return d.atm||0;
}

function applyExpirySelection(d, selectedExpiry){
  if(!d) return;
  d._primaryExpiry = d._primaryExpiry || d.expiry || '';
  d._activeExpiry = selectedExpiry || d._primaryExpiry;
  const chainStore = d.chains || {};
  const metaStore = d.chainMeta || {};

  if(!selectedExpiry || selectedExpiry === d._primaryExpiry){
    // FIX — root cause of "switch away then back to current expiry shows
    // no/stale data": this used to restore a one-time snapshot (the old
    // d._primaryChain/_primaryAtm/etc backups) taken the FIRST instant the
    // user switched away from the primary expiry, then frozen there for as
    // long as any other expiry stayed selected (the old code only ever set
    // these via `x = x || d.foo`, so later ticks could never refresh them).
    // Meanwhile every live tick kept landing on d.chain by strike-matching
    // — since d.chain was holding a swapped-in *other* expiry's rows, those
    // primary-expiry field patches silently blended into that other
    // expiry's displayed cells (see applyDelta's keyed merge in
    // market-store.js: it patches whatever array is currently sitting in
    // target.chain, not necessarily the primary expiry's own rows). So
    // switching back landed on a stale snapshot, not live data.
    //
    // d.chains[_primaryExpiry] sidesteps this: the backend always keeps a
    // separate, independently-diffed copy of the current expiry's chain
    // there (mTerminals_json.py: "chains[expiry_str] ... CURRENT chain
    // always present") and this function never writes into d.chains[...],
    // only reads from it — so it stays live and uncorrupted the entire
    // time, no matter what's briefly sitting in d.chain. Rebuild the
    // primary view from it every tick, the same way the non-primary
    // branch below rebuilds its own expiry's view, instead of trusting a
    // frozen backup.
    const primaryChainSrc = chainStore[d._primaryExpiry];
    if(primaryChainSrc && primaryChainSrc.length){
      d.chain = primaryChainSrc.map(row => Object.assign({}, row));
    }
    // atm/dte/walls/PCR/premiums/IV are plain scalars (not strike-keyed
    // arrays), so they can't suffer the in-place merge corruption above —
    // they get fully overwritten with fresh primary values by the delta
    // patch every single tick, right before this function runs. Safe to
    // keep refreshing the backup unconditionally below (no `||` guard),
    // so restoring here is never more than one tick stale.
    if(d._primaryAtm         !== undefined) d.atm         = d._primaryAtm;
    if(d._primaryDte         !== undefined) d.dte         = d._primaryDte;
    if(d._primaryCeWall      !== undefined) d.ceWall      = d._primaryCeWall;
    if(d._primaryPeWall      !== undefined) d.peWall      = d._primaryPeWall;
    if(d._primaryMaxPain     !== undefined) d.maxPain     = d._primaryMaxPain;
    if(d._primaryPCR         !== undefined) d.totalPCR    = d._primaryPCR;
    if(d._primaryCallPremium !== undefined) d.callPremium = d._primaryCallPremium;
    if(d._primaryPutPremium  !== undefined) d.putPremium  = d._primaryPutPremium;
    if(d._primaryAtmIV       !== undefined) d.atmIV       = d._primaryAtmIV;
    if(d._primaryAtmDelta    !== undefined) d.atmDelta    = d._primaryAtmDelta;
    if(d._primaryAtmGamma    !== undefined) d.atmGamma    = d._primaryAtmGamma;
    if(d._primaryAtmTheta    !== undefined) d.atmTheta    = d._primaryAtmTheta;
    if(d._primaryAtmVega     !== undefined) d.atmVega     = d._primaryAtmVega;
    if(d._primaryOiVelocity  !== undefined) d.oiVelocity  = d._primaryOiVelocity;
    // CAVEAT: greeks is ALSO a strike-keyed array, same exposure as chain
    // above — but unlike chain, the backend has no chains[expiry]-style
    // always-live mirror of the PRIMARY expiry's greeks today (chainMeta
    // only gets a "__meta__{expiry}" entry for *extra* chains — see
    // chains_by_expiry in mTerminals_json.py, which is only populated
    // inside the `if extra_chains:` loop). So this one field still relies
    // on the older one-time backup and can still go stale while a
    // non-primary expiry is shown. Needs a small backend addition
    // (mirror primary greeks into chainMeta/chains the same way chain
    // rows already are) to close fully — flagging rather than masking it.
    if(d._primaryGreeks !== undefined) d.greeks = d._primaryGreeks;
    return;
  }
  const cached = _expiryViewCache[selectedExpiry] || {};
  const selectedChainSrc = chainStore[selectedExpiry] || cached.chain;
  if(!selectedChainSrc || !selectedChainSrc.length) return;
  const selectedMeta = metaStore[selectedExpiry] || cached.meta || {};
  // IMPORTANT: never hand out the same array/row objects that live in
  // d.chains[selectedExpiry] / _expiryViewCache. d.chain gets mutated
  // in place by applyDelta() on every live WS tick (Object.assign on
  // matching-strike rows) — deltas only ever carry the primary/near
  // expiry's ticks, so if d.chain aliased the cached array, those
  // primary-expiry field patches would silently bleed into this
  // expiry's cached rows (by matching strike number) and corrupt the
  // cache until the next 'full' resync. Clone so d.chain is a
  // disposable working copy every time.
  const selectedChain = selectedChainSrc.map(row => Object.assign({}, row));
  _expiryViewCache[selectedExpiry] = { chain: selectedChainSrc, meta: selectedMeta };

  // d.chain no longer needs a backup here at all — the primary branch
  // above now rebuilds it fresh from d.chains[_primaryExpiry] every time,
  // which is what actually fixes the staleness/corruption bug. d.greeks
  // has no such mirror yet (see caveat above), so it's the one field that
  // still needs the old one-time capture — kept guarded (`||`) since
  // d.greeks may already be corrupted/swapped by the time we get here on
  // later ticks, and re-capturing then would just save the corruption.
  d._primaryGreeks = d._primaryGreeks || d.greeks;
  // The rest are plain scalars (not strike-keyed arrays), so they can't
  // pick up cross-expiry corruption the way chain/greeks can — they get
  // fully overwritten with fresh primary values by the delta patch each
  // tick, right before this function runs. Refresh every tick (no `||`
  // guard) instead of freezing at the moment of the first switch, so
  // switching back to the current expiry is never more than one tick
  // stale.
  d._primaryAtm         = d.atm;
  d._primaryDte         = d.dte;
  d._primaryCeWall      = d.ceWall;
  d._primaryPeWall      = d.peWall;
  d._primaryMaxPain     = d.maxPain;
  d._primaryPCR         = d.totalPCR;
  d._primaryCallPremium = d.callPremium;
  d._primaryPutPremium  = d.putPremium;
  d._primaryAtmIV       = d.atmIV;
  d._primaryAtmDelta    = d.atmDelta;
  d._primaryAtmGamma    = d.atmGamma;
  d._primaryAtmTheta    = d.atmTheta;
  d._primaryAtmVega     = d.atmVega;
  d._primaryOiVelocity  = d.oiVelocity;

  d.chain = selectedChain;
  const meta = selectedMeta;
  d.expiry = selectedExpiry;
  if(meta.greeks      != null) d.greeks      = meta.greeks;
  if(meta.atm         != null) d.atm         = meta.atm;
  if(meta.dte         != null) d.dte         = meta.dte;
  if(meta.ceWall      != null) d.ceWall      = meta.ceWall;
  if(meta.peWall      != null) d.peWall      = meta.peWall;
  if(meta.maxPain     != null) d.maxPain     = meta.maxPain;
  if(meta.totalPCR    != null) d.totalPCR    = meta.totalPCR;
  if(meta.straddle    != null){ d.callPremium = meta.straddle/2; d.putPremium = meta.straddle/2; }
  if(meta.callPremium != null) d.callPremium = meta.callPremium;
  if(meta.putPremium  != null) d.putPremium  = meta.putPremium;
  if(meta.atmIV       != null) d.atmIV       = meta.atmIV;
  if(meta.atmDelta    != null) d.atmDelta    = meta.atmDelta;
  if(meta.atmGamma    != null) d.atmGamma    = meta.atmGamma;
  if(meta.atmTheta    != null) d.atmTheta    = meta.atmTheta;
  if(meta.atmVega     != null) d.atmVega     = meta.atmVega;
  // ── OI VELOCITY ──
  // d.oiVelocity was never swapped per expiry before this fix, so every
  // OI-Vel view (butterfly "OI Vel" tab, Greeks/GEX panel, right-panel
  // totals) kept showing the PRIMARY expiry's velocity numbers no matter
  // which expiry was selected. This requires the backend to actually send
  // per-expiry velocity data in chainMeta[expiry].oiVelocity — if it
  // doesn't, this falls back to leaving the primary expiry's velocity in
  // place (same as before) rather than showing wrong/blank data.
  if(meta.oiVelocity  != null) d.oiVelocity  = meta.oiVelocity;
  if(!d.atm) d.atm = activeAtm(d);
}
