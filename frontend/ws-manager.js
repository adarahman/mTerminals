// ============================================================
// ws-manager.js
// Extracted verbatim from dashboard.js (Task 1 modularization —
// see master optimization prompt, Task 8 "WebSocket Manager").
// No logic changed.
//
// Owns the socket lifecycle (connect/reconnect) and the wire-format merge
// (mergeDelta) — nothing about rendering or the dashboard's own state model.
// DataService (in dashboard.js) subscribes via on('open'|'close'|'message')
// instead of inline onopen/onmessage/onclose closures, so the two concerns
// (socket plumbing vs. dashboard side-effects) can change independently.
//
// Must load BEFORE dashboard.js: DataService's constructor does
// `new WSManager(...)` at parse time (via `const app = new App()`), so the
// class needs to exist by then. See DashboardPro.html script order.
//
// Depends on the global err(m) function (defined in dashboard.js) — but
// only calls it from inside connect()/mergeDelta(), both of which only run
// after all scripts have finished loading and DOMContentLoaded/user actions
// have fired, so the cross-file reference is safe regardless of the exact
// declaration order of err() itself.
// ============================================================

// Applies one shallow/deep merge of src into target, recursing into plain
// objects and replacing arrays/primitives wholesale. Only ever called from
// WSManager.mergeDelta()'s generic (non-"full", non-"delta") branch.
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
// live _wsState in place. Unlike deepMerge, this understands the special
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

class WSManager {
  constructor(url) {
    this.url = url;
    this.ws = null;
    this.state = null;
    this.reconnectTimer = null;
    this.reconnectDelayMs = 3000;
    this.listeners = { open: [], close: [], message: [] };
  }

  on(event, fn) {
    (this.listeners[event] || (this.listeners[event] = [])).push(fn);
    return this;
  }

  emit(event, payload) {
    (this.listeners[event] || []).forEach(fn => fn(payload));
  }

  connect(url) {
    if (url) this.url = url;
    // A symbol switch (switchActiveIndex -> connectWebSocket(newUrl)) calls
    // this while a connection is already open. Without closing it first, the
    // old socket just leaks: it stays connected (browser AND server-side
    // CONNECTED set), its onclose reconnect timer can still fire, and every
    // broadcast arrives twice. onclose is nulled first so closing this one
    // doesn't itself trigger the auto-reconnect below.
    if (this.ws) { this.ws.onclose = null; try { this.ws.close(); } catch(e){} }
    try { this.ws = new WebSocket(this.url); }
    catch(e){ err('WS init error: '+e.message); return; }

    this.ws.onopen = () => this.emit('open');

    this.ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); }
      catch(e){ err('WS parse error: '+e.message); return; }
      this.mergeDelta(msg);
      this.emit('message', this.state);
    };

    this.ws.onclose = () => {
      this.emit('close');
      this.reconnect();
    };

    this.ws.onerror = () => { /* onclose fires next and triggers reconnect */ };
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.ws) { this.ws.onclose = null; try { this.ws.close(); } catch(e){} this.ws = null; }
  }

  reconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.connect(), this.reconnectDelayMs);
  }

  // Applies one wire message to this.state, mutating/replacing it as needed.
  // Pulled verbatim out of the old updateDashboard() — same three message
  // shapes, same portfolio/orders carry-over on 'full' resync.
  mergeDelta(msg) {
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
    return this.state;
  }
}
