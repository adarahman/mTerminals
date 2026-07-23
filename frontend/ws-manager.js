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
    // close() is not instantaneous — it starts a closing handshake, and the
    // browser can still deliver an already-in-flight (or last-buffered)
    // message through the OLD socket's onmessage handler even after close()
    // has been called. Previously only onclose was nulled here, so a stale
    // socket mid-close could still fire onmessage -> emit('message', ...)
    // with data for whatever expiry/symbol it was last serving, racing
    // against (and sometimes landing after) the new socket's correct
    // payload. Null out every handler on the old socket, not just onclose.
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onmessage = null;
      this.ws.onopen = null;
      this.ws.onerror = null;
      try { this.ws.close(); } catch(e){}
    }
    let socket;
    try { socket = new WebSocket(this.url); }
    catch(e){ err('WS init error: '+e.message); return; }
    this.ws = socket;

    // Belt-and-suspenders: even with the handlers above nulled, guard by
    // identity too. If `socket` (captured in this closure) is no longer
    // this.ws by the time a handler fires — e.g. connect() ran again before
    // this particular handler got cleared — the event is silently dropped
    // instead of being emitted.
    socket.onopen = () => { if (this.ws === socket) this.emit('open'); };

    socket.onmessage = (event) => {
      if (this.ws !== socket) return;
      let msg;
      try { msg = JSON.parse(event.data); }
      catch(e){ err('WS parse error: '+e.message); return; }
      // Raw parsed message, unmerged — MarketStore.ingest() interprets it.
      this.emit('message', msg);
    };

    socket.onclose = () => {
      if (this.ws !== socket) return;
      this.emit('close');
      this.reconnect();
    };

    socket.onerror = () => { /* onclose fires next and triggers reconnect */ };
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
