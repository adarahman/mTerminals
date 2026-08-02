// ============================================================
// market-store.js
// Phase 1 architecture split (see master optimization prompt):
// "Introduce a centralized MarketStore to own _wsState" +
// "Move merge/patch logic (applyDelta, deepMerge) out of WSManager."
//
// MarketStore is the single owner of the live market/portfolio state
// object that the rest of the dashboard reads as `_wsState`. It knows
// nothing about sockets — it only knows how to interpret one parsed wire
// message (full / delta / generic) and fold it into `this.state`, then
// notify subscribers. WSManager hands it raw messages via 'message';
// DataService.updateDashboard() subscribes to MarketStore's 'change'
// event instead of reading WSManager.state directly.
//
// Must load AFTER ws-manager.js and BEFORE dashboard.js (DataService's
// constructor instantiates both at parse time). See DashboardPro.html
// script order.
// ============================================================

// Applies one shallow/deep merge of src into target, recursing into plain
// objects and replacing arrays/primitives wholesale. Only ever called from
// MarketStore.ingest()'s generic (non-"full", non-"delta") branch.
function deepMerge(target, src){
  if(!src || typeof src!=='object') return target;
  for(const k in src){
    if(src[k] && typeof src[k]==='object' && !Array.isArray(src[k]) &&
       target[k] && typeof target[k]==='object' && !Array.isArray(target[k])){
      deepMerge(target[k], src[k]);
    } else {
      target[k]=src[k]; // arrays (e.g. chain) and primitives are replaced wholesale
    }
  }
  return target;
}

// Applies a diff produced by ws_server_live.py's compute_diff() onto the
// live state in place. Unlike deepMerge, this understands the special
// {_keyed, _key_field, changed, _removed_keys} shape compute_diff uses for
// arrays of dicts (e.g. the option chain, keyed by "strike"), plus the
// {_removed:[...]} marker for deleted object keys — deepMerge would just
// overwrite target[k] with that metadata object instead of patching it.
function applyDelta(target, diff, keyField='strike'){
  if(!diff || typeof diff !== 'object') return target;
  for(const k in diff){
    if(k === '_removed') continue;
    const v = diff[k];
    if(v && typeof v === 'object' && v._keyed){
      const kf = v._key_field || keyField;
      const arr = Array.isArray(target[k]) ? target[k] : [];
      const byKey = new Map(arr.map(row => [row[kf], row]));
      for(const row of v.changed){
        const existingRow = byKey.get(row[kf]);
        if(existingRow){
          // Patch only the fields present in this delta (e.g. SmartAPI's
          // partial ceLTP/ceOI ticks) instead of replacing the whole row —
          // a wholesale replace here was wiping out ce_oi_chg/net_oi/Greeks
          // for any strike a partial tick touched, until the next full
          // NSE-driven compute_diff() cycle happened to restore them.
          Object.assign(existingRow, row);
        } else {
          byKey.set(row[kf], row); // brand-new row, nothing to merge into
        }
      }
      if(v._removed_keys) for(const rk of v._removed_keys) byKey.delete(rk);
      const existing = new Set(arr.map(row => row[kf]));
      const patched = arr.filter(row => byKey.has(row[kf])).map(row => byKey.get(row[kf]));
      for(const row of v.changed) if(!existing.has(row[kf])) patched.push(row);
      target[k] = patched;
    } else if(v && typeof v === 'object' && !Array.isArray(v)){
      if(!target[k] || typeof target[k] !== 'object') target[k] = {};
      applyDelta(target[k], v, keyField);
    } else {
      target[k] = v; // scalar or wholesale-replaced list
    }
  }
  if(diff._removed) for(const rk of diff._removed) delete target[rk];
  return target;
}

class MarketStore {
  constructor() {
    this.state = null;
    this.listeners = { change: [] };
  }

  on(event, fn) {
    (this.listeners[event] || (this.listeners[event] = [])).push(fn);
    return this;
  }

  emit(event, payload) {
    (this.listeners[event] || []).forEach(fn => fn(payload));
  }

  // Feed one raw parsed WS message in. Moved verbatim out of the old
  // WSManager.mergeDelta() — same three message shapes, same
  // portfolio/orders carry-over on 'full' resync — just renamed to make
  // clear it's the store's ingestion point, not a socket concern.
  ingest(msg) {
    if (!msg || !msg.type) { // backend sent a plain full snapshot, no envelope
      this.state = msg;
    } else if (msg.type === 'full') {
      // 'full' replaces the entire market-data payload wholesale — but
      // portfolio/orders are a separate feed (PT_ENGINE), not part of
      // LAST_PAYLOAD, and only get re-sent on connect or on the next
      // place_order. A 'full' resync fires any time _LAST_SENT resets
      // server-side (startup, or switch_symbol() on a ticker-pill click),
      // and used to silently wipe state.portfolio/state.orders back to
      // undefined, freezing the paper trading panel on '—' until the next
      // order was placed. Carry them across the replace.
      const prevPortfolio = this.state && this.state.portfolio;
      const prevOrders = this.state && this.state.orders;
      this.state = msg.payload;
      if(prevPortfolio !== undefined) this.state.portfolio = prevPortfolio;
      if(prevOrders !== undefined) this.state.orders = prevOrders;
    } else if (msg.type === 'delta') {
      // ws_server_live.py's compute_diff() output — must be patched with
      // applyDelta, not merged under a literal "delta" key like the generic
      // branch below would do.
      if(!this.state) this.state = {};
      applyDelta(this.state, msg.payload);
    } else {
      if(!this.state) this.state = {};
      deepMerge(this.state, {[msg.type]: msg.payload});
    }
    this.emit('change', this.state);
    // Phase 5 (event-bus.js): same merged state, published on the shared
    // bus as 'market:update' alongside the existing 'change' emit above —
    // DataService's store.on('change', ...) subscription (data-service.js)
    // is untouched, so this is additive only.
    if (window.eventBus) window.eventBus.emit('market:update', this.state);
    return this.state;
  }
}
