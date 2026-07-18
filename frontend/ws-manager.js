// ============================================================
// ws-manager.js
// Extracted verbatim from dashboard.js (Task 1 modularization —
// see master optimization prompt, Task 8 "WebSocket Manager").
//
// Phase 1 (MarketStore split): the wire-format merge logic that used to
// live here (deepMerge/applyDelta/mergeDelta, plus the .state field they
// mutated) has moved to market-store.js's MarketStore class. WSManager now
// owns ONLY the socket lifecycle — connect/reconnect/close — and emits
// each parsed message as-is via 'message'. It has no opinion about what a
// "full" vs "delta" message means or how state should be assembled; that's
// MarketStore's job. This keeps socket plumbing free to change (e.g. swap
// transport, add heartbeats) without touching merge logic, and vice versa.
//
// Must load BEFORE dashboard.js: DataService's constructor does
// `new WSManager(...)` at parse time (via `const app = new App()`), so the
// class needs to exist by then. See DashboardPro.html script order.
//
// Depends on the global err(m) function (defined in dashboard.js) — but
// only calls it from inside connect(), which only runs after all scripts
// have finished loading and DOMContentLoaded/user actions have fired, so
// the cross-file reference is safe regardless of exact declaration order.
// ============================================================

class WSManager {
  constructor(url) {
    this.url = url;
    this.ws = null;
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
      // Raw parsed message, unmerged — MarketStore.ingest() interprets it.
      this.emit('message', msg);
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
}
