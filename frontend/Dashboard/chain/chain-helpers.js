// Shared option-chain calculations, formatting and payoff helpers.
// Market/symbol/expiry state belongs in market-context.js.

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

// refPrice (spot or ATM strike) is optional for backward compatibility, but
// callers should always pass it: a real chain frequently has MORE THAN ONE
// zero-crossing — e.g. a stray near-zero-OI strike out past the wings, or
// a second genuine crossing far OTM — and scanning strike-by-strike from
// the bottom of the chain and returning the very first one found (the old
// behavior) picks whichever crossing happens to sit at the lowest strike,
// not the one that actually matters for dealer positioning: the crossing
// nearest current price. Without refPrice this still returns the first
// crossing found, same as before.
function findGammaFlipStrike(arr, refPrice){
  if (!arr || arr.length < 2) return null;

  const crossings = [];

  for(let i=0;i<arr.length-1;i++){

    const g1 = arr[i];
    const g2 = arr[i+1];

    const v1 = g1.netGEX || 0;
    const v2 = g2.netGEX || 0;

    if(Math.abs(v1) < GEX_FLIP_EPS){
      crossings.push({ strike: g1.strike, netGEX: 0, between: false });
      continue;
    }

    if(v1 * v2 < 0){

      // Linear interpolation
      const ratio = Math.abs(v1) / (Math.abs(v1)+Math.abs(v2));

      const flipStrike =
        g1.strike +
        ratio * (g2.strike - g1.strike);

      crossings.push({
        strike: flipStrike,
        netGEX:0,
        between:true
      });
    }
  }

  if (!crossings.length) return null;
  if (refPrice == null) return crossings[0]; // old behavior when no reference given

  let best = crossings[0];
  let bestDist = Math.abs(best.strike - refPrice);
  for (let i = 1; i < crossings.length; i++){
    const d = Math.abs(crossings[i].strike - refPrice);
    if (d < bestDist){ bestDist = d; best = crossings[i]; }
  }
  return best;
}

// Shared implementation for chainCombinedSignal()/combinedSignal() below —
// same CE+PE bias math, same six-way branching, previously duplicated
// verbatim with only the CSS class prefix differing ('sig-' for the dense/
// right-panel view and the main chain table's per-row signal badge, 'sp-'
// for chain-renderer.js's summary strip). Merged into one function that
// takes the prefix as a parameter; call sites in chain-depth.js,
// chain-view-models.js, and chain-renderer.js are unchanged.
function _combinedSignalWithPrefix(ceSignal, peSignal, prefix){
  const cb = ceBias(ceSignal), pb = peBias(peSignal);
  const sum = cb + pb;
  if (cb > 0 && pb > 0) return { label: 'Strong Bullish', cls: prefix + 'strongbull' };
  if (cb < 0 && pb < 0) return { label: 'Strong Bearish', cls: prefix + 'strongbear' };
  if (cb !== 0 && pb !== 0) return { label: 'Mixed', cls: prefix + 'mixed' };
  if (sum > 0) return { label: 'Bullish', cls: prefix + 'bull' };
  if (sum < 0) return { label: 'Bearish', cls: prefix + 'bear' };
  return { label: 'Neutral', cls: prefix + 'n' };
}

function chainCombinedSignal(ceSignal, peSignal){
  return _combinedSignalWithPrefix(ceSignal, peSignal, 'sig-');
}

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
  return _combinedSignalWithPrefix(ceSignal, peSignal, 'sp-');
}

// Single source of truth for "is this row/dataset bearish (or bullish)",
// replacing three independent copies (chain-renderer.js x2, chain-
// template.js, exec-view.js) that had drifted apart: two of them OR'd the
// compositeBias substring match in unconditionally, so a decision engine
// output of BULLISH/NEUTRAL/CONFLICTED could still be overridden by a
// stray "bear" substring in compositeBias. That's wrong when decision.bias
// is present and authoritative — compositeBias should only be consulted
// as a fallback when decision.bias is missing entirely, which is the
// behavior standardized here (previously only exec-view.js did this).
function isBearBias(d){
  const decBias = (d.decision && d.decision.bias) || '';
  return decBias === 'BEARISH' || (!decBias && (d.compositeBias||'').toLowerCase().includes('bear'));
}
function isBullBias(d){
  const decBias = (d.decision && d.decision.bias) || '';
  return decBias === 'BULLISH' || (!decBias && (d.compositeBias||'').toLowerCase().includes('bull'));
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
