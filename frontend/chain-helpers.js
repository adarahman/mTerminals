// ============================================================
// chain-helpers.js
// Phase 1 bootstrap cleanup (see master optimization prompt, Task
// "Dashboard bootstrap cleanup"): dashboard.js is meant to hold ONLY app
// init/wiring/coordination now. Everything here is shared chain/expiry/
// index domain logic — pure functions plus a handful of small pieces of
// module-level state (the expiry-select node cache, the symbol list) that
// chain-views.js and panels-views.js already call as globals. None of it
// is bootstrap concern, so it's been pulled out verbatim.
//
// Load position: after formatters.js/dom-utils.js, before chain-views.js/
// panels-views.js — matching where those files' own header comments say
// their shared helpers belong. Nothing here is actually invoked at parse
// time (only from render/interaction callbacks), so exact ordering
// relative to chain-views.js/panels-views.js isn't load-bearing, but this
// keeps the script list readable top-to-bottom. See DashboardPro.html
// script order.
// ============================================================

// Finds the strike where dealer net GEX crosses zero (short γ -> long γ or
// vice versa). Was previously `arr.find((g,i)=>i>0&&Math.sign(g.netGEX)!==
// Math.sign(arr[i-1].netGEX))` — but the full strike list (n_strikes_each_
// side defaults to 999 in engine.py) includes plenty of deep OTM/ITM
// strikes with zero OI on both legs, where netGEX is exactly 0.
// Math.sign(0) is 0, which is neither 1 nor -1, so the very first boundary
// between "no OI, netGEX===0" and "any real OI at all" got flagged as a
// sign change — producing a "flip strike" far from spot that had nothing
// to do with an actual short/long gamma crossover. Skipping near-zero
// (no real exposure) strikes fixes that.
const GEX_FLIP_EPS = 1e-6;
function findGammaFlipStrike(arr){
  if (!arr || !arr.length) return null;
  let prevSign = null;
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i].netGEX || 0;
    if (Math.abs(v) < GEX_FLIP_EPS) continue; // no real exposure at this strike — skip, don't compare
    const s = Math.sign(v);
    if (prevSign !== null && s !== prevSign) return arr[i];
    prevSign = s;
  }
  return null;
}

function chainCombinedSignal(ceSignal, peSignal){
  const cb = ceBias(ceSignal), pb = peBias(peSignal);
  const sum = cb + pb;
  if (cb > 0 && pb > 0) return { label: 'Strong Bullish', cls: 'sig-strongbull' };
  if (cb < 0 && pb < 0) return { label: 'Strong Bearish', cls: 'sig-strongbear' };
  if (cb !== 0 && pb !== 0) return { label: 'Mixed', cls: 'sig-mixed' };
  if (sum > 0) return { label: 'Bullish', cls: 'sig-bull' };
  if (sum < 0) return { label: 'Bearish', cls: 'sig-bear' };
  return { label: 'Neutral', cls: 'sig-n' };
}

// ── INDEX TICKER STRIP (NIFTY / BANKNIFTY / MIDCPNIFTY / SENSEX) ──
// Fixed left-to-right order — NIFTY always first, regardless of which
// symbol the dashboard is currently connected to. Only .idx-pill.active
// (a highlight ring) reflects selection state; position never does.
const INDEX_TICKER_ORDER = ['NIFTY','BANKNIFTY','MIDCPNIFTY','SENSEX'];

// Reads live quotes from d.indexQuotes = { NIFTY:{spot,spotChange,spotChgPct}, ... }
// pushed by ws_server_live.py's index_quote_loop() (see INDEX_QUOTES there —
// key names must match exactly, this was previously reading a `chgPct`
// field that the backend never sends, so every non-active pill silently
// showed 0.00% forever instead of the real change).
// The active symbol is deliberately left OUT of this strip — its spot/
// change is already the big readout immediately to the left, so repeating
// it as a same-size pill here was pure duplication. A VIX pill takes that
// same first slot instead (relocated from the old expiry-strip VIX pill),
// which is more useful screen space than a second copy of the number
// already showing. The remaining (non-active) indices show a "—"
// placeholder for % change until indexQuotes is wired up on the backend.
// In your dashboard.js — Updated render function
function renderIndexTicker(d) {
  if (!d) d = {};

  // The 'indices' array comes from your new unified stream in mTerminals_json.py
  const indices = d.allIndices || [];
  const active = d.symbol || 'NIFTY';

  // VIX Logic remains the same, assuming d.indiaVix is still passed.
  // %change badge — reads d.indiaVixChgPct, which mTerminals_json.py sends
  // as ctx_dict["india_vix_chg_pct"] (defaults to 0.0 upstream if that
  // context field isn't populated yet — so a real 0 and "not wired up"
  // will look identical until india_vix_chg_pct is actually computed).
  const vixRegime = (d.vixRegime || '').toLowerCase();
  const vixColor = vixRegime === 'high' ? '#FF6B6B' : vixRegime === 'low' ? '#20C997' : '#FFD43B';
  const vixUp = (d.indiaVixChgPct || 0) >= 0;
  const vixChgHtml = d.indiaVixChgPct !== undefined
    ? `<span class="idx-pill-chg ${vixUp ? 'up' : 'down'}">${vixUp ? '▲' : '▼'}${Math.abs(d.indiaVixChgPct).toFixed(2)}%</span>`
    : '';
  const vixPill = `<div class="idx-pill idx-pill-vix" title="India VIX">
    <span class="idx-pill-sym">VIX</span>
    <span class="idx-pill-val" style="color:${vixColor};">${fmtN(d.indiaVix, 1)}</span>
    ${vixChgHtml}
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

      return `<div class="idx-pill" onclick="switchActiveIndex('${backendSymbol}')" title="Switch to ${displayName}">
        <span class="idx-pill-sym">${displayName}</span>
        <span class="idx-pill-val">${fmtI(idx["Last Price"])}</span>
        <span class="idx-pill-chg ${up ? 'up' : 'down'}">${up ? '▲' : '▼'}${Math.abs(pChange).toFixed(2)}%</span>
      </div>`;
    }).join('');

  return `<div class="index-ticker" id="index-ticker-bar">${vixPill}${pills}</div>`;
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
    const res = await fetch('/api/symbols');
    if(!res.ok) return;
    const list = await res.json();
    if(Array.isArray(list) && list.length){
      COMMON_SYMBOLS.length = 0;
      COMMON_SYMBOLS.push(...list);
    }
  }catch(e){
    console.warn('[symbols] /api/symbols fetch failed, keeping seed list', e);
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
  // If you need to hit a specific API before connecting:
  // fetch(`http://api/set_index?symbol=${sym}`).then(...)
  
  // Default behavior
  const base = (_wsUrl || '').split('?')[0];
  connectWebSocket(`${base}?symbol=${encodeURIComponent(sym)}`);
  // Phase 5 (event-bus.js): announce the switch on the shared bus. Purely
  // additive — connectWebSocket() above is still the only thing that
  // actually performs the switch; this just gives other modules a way to
  // react to it later without switchActiveIndex needing to know who.
  if (window.eventBus) window.eventBus.emit('symbol:change', { symbol: sym });
}
window.switchActiveIndex = switchActiveIndex;
window.renderIndexTicker = renderIndexTicker;

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
const _CP1252_REV = {0x20AC:0x80,0x201A:0x82,0x0192:0x83,0x201E:0x84,0x2026:0x85,0x2020:0x86,0x2021:0x87,
  0x02C6:0x88,0x2030:0x89,0x0160:0x8A,0x2039:0x8B,0x0152:0x8C,0x017D:0x8E,0x2018:0x91,0x2019:0x92,
  0x201C:0x93,0x201D:0x94,0x2022:0x95,0x2013:0x96,0x2014:0x97,0x02DC:0x98,0x2122:0x99,0x0161:0x9A,
  0x203A:0x9B,0x0153:0x9C,0x017E:0x9E,0x0178:0x9F};
function _fixMojibake(s){
  if(typeof s!=='string' || !/[ÂÃâ]/.test(s)) return s;
  const bytes=[];
  for(const ch of s){
    const cp=ch.codePointAt(0);
    if(cp<=0xFF) bytes.push(cp);
    else if(_CP1252_REV.hasOwnProperty(cp)) bytes.push(_CP1252_REV[cp]);
    else return s; // a character here can't be a mis-decoded single byte — not mojibake, bail out
  }
  try{
    return new TextDecoder('utf-8',{fatal:true}).decode(new Uint8Array(bytes));
  }catch(e){
    return s; // not actually mojibake — leave as-is
  }
}
function _fixMojibakeDeep(obj, depth){
  if(depth===undefined) depth=0;
  if(depth>6 || obj==null) return obj;
  if(typeof obj==='string') return _fixMojibake(obj);
  if(Array.isArray(obj)){ for(let i=0;i<obj.length;i++) obj[i]=_fixMojibakeDeep(obj[i],depth+1); return obj; }
  if(typeof obj==='object'){ for(const k in obj) obj[k]=_fixMojibakeDeep(obj[k],depth+1); return obj; }
  return obj;
}

function spClass(s){
  if(!s)return'sp-n';
  s=s.toLowerCase();
  if(s.includes('long build')||s.includes('buying')||s.includes('lb'))return'sp-lb';
  if(s.includes('short cover')||s.includes('covering')||s.includes('sc'))return'sp-sc';
  if(s.includes('short build')||s.includes('writing')||s.includes('unwind')||s.includes('sb'))return'sp-sb';
  return'sp-n';
}

function ceBias(s){
  if(!s)return 0;
  s=s.toLowerCase();
  if(s.includes('writing')||s.includes('short build'))return -1;
  if(s.includes('unwind')||s.includes('cover'))return 1;
  return 0;
}

function peBias(s){
  if(!s)return 0;
  s=s.toLowerCase();
  if(s.includes('writing')||s.includes('short build')||s.includes('buying')||s.includes('long build'))return 1;
  if(s.includes('unwind')||s.includes('cover'))return -1;
  return 0;
}

function combinedSignal(ceSignal,peSignal){
  const cb=ceBias(ceSignal),pb=peBias(peSignal);
  const sum=cb+pb;
  if(cb>0&&pb>0)return{label:'Strong Bullish',cls:'sp-strongbull'};
  if(cb<0&&pb<0)return{label:'Strong Bearish',cls:'sp-strongbear'};
  if(cb!==0&&pb!==0)return{label:'Mixed',cls:'sp-mixed'};
  if(sum>0)return{label:'Bullish',cls:'sp-bull'};
  if(sum<0)return{label:'Bearish',cls:'sp-bear'};
  return{label:'Neutral',cls:'sp-n'};
}

function biasCls(b){
  if(!b)return'badge-neutral';
  b=b.toLowerCase();
  if(b.includes('bull'))return'badge-bull';
  if(b.includes('bear'))return'badge-bear';
  return'badge-neutral';
}

function pcrCls(p){return p>1.3?'badge-bull':p<0.8?'badge-bear':'badge-neutral';}

// Parses "DD-MMM-YYYY" (e.g. "07-AUG-2026") into a sortable timestamp.
// Falls back to Date.parse for any other format, and to +Infinity (sorts
// last, stable-ish) if the string is unparseable — so a stray bad entry
// can't crash the sort or silently reorder everything around it.
const _EXPIRY_MONTHS = {JAN:0,FEB:1,MAR:2,APR:3,MAY:4,JUN:5,JUL:6,AUG:7,SEP:8,OCT:9,NOV:10,DEC:11};
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

function getFilteredChain(d){
  const chainAll=(d&&d.chain)||[];
  if(_chainRange===9999) return chainAll;
  const atm=activeAtm(d);
  const idx=chainAll.findIndex(r=>r.atm||r.strike===atm);
  return idx<0?chainAll:chainAll.filter((r,i)=>Math.abs(i-idx)<=_chainRange);
}

function velMiniCell(v,maxAbs,clr){
  v=v||0;
  const pct=maxAbs>0?Math.max(Math.min(Math.abs(v)/maxAbs*24,24),2):2;
  return `<div class="vel-mini-wrap"><div class="vel-mini-bar" style="width:${pct.toFixed(0)}px;background:${clr};"></div><span class="vel-mini-val" style="color:${clr};">${v>=0?'+':''}${fmtK(v)}</span></div>`;
}

// Builds the strike-by-strike butterfly rows for the OI Flow chart.
// mode: 'oi' (open interest) | 'chg' (intraday OI change) | 'vel' (OI velocity, current _velWin)
function oiFlowLabel(mode){
  return mode==='chg'?'OI Chg':mode==='vel'?`OI Vel (${_velWin}m)`:'OI';
}
// Single-line highlight of the biggest CE build and biggest PE build in the
// visible strike range — the rest of that detail already lives in the
// butterfly chart itself (switch to the "OI Chg" tab), so this stays a
// 1-line callout rather than repeating the full ranked list.
// mode-aware: pulls the "biggest build" figure from whichever series is
// currently shown in the butterfly chart (OI / OI Chg / OI Vel), instead of
// always reading the raw intraday OI-change column.

// ── TOP 3 DRIVERS / DRAGGERS ──
// Expects d.contributors = [{ symbol, pointImpact (or point_impact),
// pctChange (or pct_change) }, ...] — one entry per index heavyweight,
// as produced by DraggerDriver.py / derive_top_contributors(). Renders
// a placeholder until the backend actually populates that field on the
// WS payload. Returned as the 3rd exec-card, sitting alongside Market
// Health / Market Story in the same exec-grid row.

// ── EXPIRY CHANGE HANDLER ──
function _dteFromStr(dateStr){
  // Parse "DD-Mon-YYYY" → calendar days from today
  if(!dateStr) return 0;
  try{
    const months={Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};
    const parts=dateStr.split('-');
    if(parts.length!==3) return 0;
    const d=parseInt(parts[0],10);
    const m=months[parts[1]];
    const y=parseInt(parts[2],10);
    if(m===undefined||isNaN(d)||isNaN(y)) return 0;
    const expDt=new Date(y,m,d);
    const today=new Date(); today.setHours(0,0,0,0);
    return Math.max(0,Math.round((expDt-today)/(1000*60*60*24)));
  }catch(e){return 0;}
}

// Compute per-strike P&L at expiry for one leg
function _legPnl(leg, underlyingAtExpiry, lotSize){
  const S   = underlyingAtExpiry;
  const K   = leg.strike;
  const ltp = parseFloat(leg.ltp) || 0;
  const lots= leg.lots || 1;
  const dir = leg.action === 'BUY' ? 1 : -1;
  const type= (leg.type||'').toUpperCase();
  let payoff= 0;
  if(type==='CE') payoff = Math.max(S - K, 0) - ltp;
  else if(type==='PE') payoff = Math.max(K - S, 0) - ltp;
  else if(type==='FUT') payoff = S - K - ltp;
  return dir * payoff * lots * lotSize;
}
