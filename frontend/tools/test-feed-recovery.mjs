import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');

function loadWsManager() {
  const sockets = [];
  const timers = new Map();
  let nextTimer = 1;
  class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    constructor(url) {
      this.url = url;
      this.closed = false;
      this.readyState = FakeWebSocket.CONNECTING;
      sockets.push(this);
    }
    close() { this.closed = true; this.readyState = FakeWebSocket.CLOSED; }
  }
  const context = vm.createContext({
    WebSocket: FakeWebSocket,
    err() {},
    setTimeout(fn) { const id = nextTimer++; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(`${read('shared/services/ws-manager.js')}\nthis.WSManager = WSManager;`, context);
  return { WSManager: context.WSManager, sockets, timers };
}

{
  const { WSManager, sockets, timers } = loadWsManager();
  const events = [];
  const manager = new WSManager('ws://example.test/ws');
  manager.reconnectDelayMs = 25;
  manager.on('open', () => events.push('open'));
  manager.on('close', () => events.push('close'));
  manager.on('message', msg => events.push(msg.type));

  manager.connect();
  assert.equal(sockets.length, 1, 'initial socket must be created');
  sockets[0].readyState = 1;
  sockets[0].onopen();
  assert.deepEqual(events, ['open']);

  sockets[0].onclose();
  assert.deepEqual(events, ['open', 'close']);
  assert.equal(timers.size, 1, 'close must schedule one reconnect');
  const [reconnectId, reconnectFn] = [...timers.entries()][0];
  timers.delete(reconnectId); // real timers leave the pending queue before invoking
  reconnectFn();
  assert.equal(sockets.length, 2, 'reconnect timer must create a replacement socket');
  assert.match(sockets[1].url, /[?&]reconnect=1$/, 'automatic reconnect must identify itself to metrics');

  sockets[1].readyState = 1;
  sockets[1].onopen();
  sockets[1].onmessage({ data: JSON.stringify({ type: 'full', payload: {} }) });
  assert.deepEqual(events, ['open', 'close', 'open', 'full']);

  sockets[1].onclose();
  assert.equal(timers.size, 1);
  manager.connect('ws://example.test/ws?symbol=BANKNIFTY');
  assert.equal(manager.reconnectTimer, null, 'explicit connect must cancel an older reconnect timer');
  assert.equal(timers.size, 1, 'replacement socket must retain only its connect watchdog');
  assert.equal(sockets.length, 3);
}

function loadDataService() {
  let now = 1_000_000;
  class FakeDate extends Date { static now() { return now; } }
  class FakeWSManager {
    constructor(url) { this.url = url; this.listeners = {}; this.connectCalls = []; }
    on(name, fn) { (this.listeners[name] ||= []).push(fn); return this; }
    emit(name, value) { for (const fn of this.listeners[name] || []) fn(value); }
    connect(url, reconnect) { this.connectCalls.push({ url, reconnect }); }
    ensureConnected(force) { this.connectCalls.push({ ensure: force }); }
  }
  class FakeMarketStore {
    constructor() { this.listeners = {}; }
    on(name, fn) { (this.listeners[name] ||= []).push(fn); return this; }
    ingest(value) { this.lastIngested = value; }
  }
  const statuses = [];
  const eventBus = { emit(name, value) { if (name === 'feed:status') statuses.push(value.status); } };
  const context = vm.createContext({
    Date: FakeDate,
    Config: { refresh: { defaultAutoRefreshMins: 5 }, ws: {
      url: 'ws://test', staleAfterMs: 12_000, staleRecoveryCooldownMs: 12_000,
    } },
    AppState: { feedState: null },
    WSManager: FakeWSManager,
    MarketStore: FakeMarketStore,
    window: { eventBus, addEventListener() {} },
    document: { addEventListener() {}, visibilityState: 'visible' },
    setInterval() { return 1; },
    clearInterval() {},
    setTimeout() { return 2; },
    clearTimeout() {},
    err() {},
    $i() { return null; },
  });
  vm.runInContext(`${read('shared/services/data-service.js')}\nthis.DataService = DataService;`, context);
  return { DataService: context.DataService, AppState: context.AppState, statuses, advance(ms) { now += ms; } };
}

{
  const { DataService, AppState, statuses, advance } = loadDataService();
  const service = new DataService();
  assert.equal(AppState.feedState.status, 'CONNECTING');

  service.wsManager.emit('open');
  assert.equal(AppState.feedState.status, 'RECOVERING', 'open socket must wait for coherent data');
  service.wsManager.emit('close');
  assert.equal(AppState.feedState.status, 'DISCONNECTED');
  assert.equal(AppState.feedState.reason, 'WebSocket closed');

  service.wsManager.emit('open');
  assert.equal(AppState.feedState.status, 'RECOVERING');
  service.wsManager.emit('message', { type: 'portfolio', payload: { positions: [] } });
  assert.equal(AppState.feedState.status, 'RECOVERING', 'auxiliary traffic must not claim market recovery');
  assert.equal(AppState.feedState.lastMessageAt, null, 'auxiliary traffic must not move the market clock');
  assert.equal(AppState.feedState.lastTransportAt, 1_000_000, 'auxiliary traffic still records transport activity');
  service.wsManager.emit('message', { type: 'full', payload: {} });
  assert.equal(AppState.feedState.status, 'LIVE', 'first message must restore LIVE');

  advance(12_001);
  service._checkFeedFreshness();
  assert.equal(AppState.feedState.status, 'RECOVERING');
  assert.match(AppState.feedState.reason, /No market snapshot for 12s/);
  assert.deepEqual(service.wsManager.connectCalls, [{ url: undefined, reconnect: true }]);

  const staleMarketAt = AppState.feedState.lastMessageAt;
  service.wsManager.emit('message', { type: 'algoStatus', payload: { running: true } });
  assert.equal(AppState.feedState.status, 'RECOVERING', 'auxiliary traffic must not conceal stale market data');
  assert.equal(AppState.feedState.lastMessageAt, staleMarketAt);

  service.wsManager.emit('message', { type: 'delta', payload: {} });
  assert.equal(AppState.feedState.status, 'LIVE', 'fresh message must recover from STALE');
  assert.equal(AppState.feedState.reason, '');

  service.beginSymbolSwitch('BANKNIFTY');
  service.store.lastIngested = null;
  service.wsManager.emit('message', { type: 'delta', payload: { spot: 50000 } });
  assert.equal(service.store.lastIngested, null, 'a new symbol must not inherit a delta from the old baseline');
  service.wsManager.emit('message', { type: 'full', payload: { symbol: 'NIFTY', spot: 24000 } });
  assert.equal(service.store.lastIngested, null, 'a stale full snapshot for the old symbol must be ignored');
  const bankSnapshot = { type: 'full', payload: { symbol: 'BANKNIFTY', spot: 50000 } };
  service.wsManager.emit('message', bankSnapshot);
  assert.equal(service.store.lastIngested, bankSnapshot, 'the requested symbol full snapshot must establish the new baseline');
  assert.equal(service.pendingSymbol, null);
  assert.deepEqual(statuses, [
    'CONNECTING', 'RECOVERING', 'DISCONNECTED', 'RECOVERING',
    'LIVE', 'STALE', 'RECOVERING', 'LIVE', 'RECOVERING', 'LIVE',
  ], 'shared consumers must receive both recovery transitions');
}

console.log('PASS  WebSocket disconnect schedules one reconnect');
console.log('PASS  Explicit connect cancels an obsolete reconnect timer');
console.log('PASS  Open socket remains RECOVERING until data arrives');
console.log('PASS  Feed transitions LIVE → STALE → LIVE deterministically');
console.log('PASS  Auxiliary traffic cannot refresh market freshness');
console.log('PASS  A stale half-open socket is replaced with a reconnect baseline');
console.log('PASS  Symbol handoff rejects stale prices until the matching baseline');
console.log('\n7/7 feed recovery checks passed.');
