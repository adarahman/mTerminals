const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const { test } = require('node:test');

function setup() {
  const connections = [];
  const timers = new Map();
  const states = [];
  let nextTimer = 0;
  class Socket {
    static OPEN = 1;
    static CONNECTING = 0;
    readyState = 0;
    constructor() { connections.push(this); }
    close() { this.readyState = 3; }
  }
  const context = {
    exports: {}, WebSocket: Socket, console: { log() {}, warn() {} },
    setTimeout(fn, delay) { timers.set(++nextTimer, { fn, delay }); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
    require() { return { marketStore: {
      setConnectionStatus(...args) { states.push(args); }, setPayload() {}, setError() {},
    } }; },
  };
  const source = fs.readFileSync(path.join(__dirname, '../src/services/mterminals-ws.ts'), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, context);
  return { api: context.exports, connections, timers, states };
}

test('late close from an old socket does not disconnect its replacement', () => {
  const { api, connections, states } = setup();
  api.connectMTerminalsWS('ws://example/mobile-ws');
  const old = connections[0];
  api.reconnectMTerminalsWS();
  const current = connections[1];
  current.readyState = 1;
  current.onopen();
  old.onclose();
  assert.equal(states.at(-1)[0], 'connected');
  let sent;
  current.send = (message) => { sent = message; };
  assert.equal(api.sendMTerminalsWS({ action: 'ping' }), true);
  assert.equal(sent, '{"action":"ping"}');
});

test('repeated connection failures do not postpone the explanatory error', () => {
  const { api, connections, timers, states } = setup();
  api.connectMTerminalsWS('ws://example/mobile-ws');
  connections[0].onerror();
  const hardError = [...timers.values()].find(timer => timer.delay === 5000);
  connections[0].onclose();
  assert.equal([...timers.values()].find(timer => timer.delay === 5000), hardError);
  hardError.fn();
  assert.equal(states.at(-1)[0], 'error');
  assert.match(states.at(-1)[1], /Start Backend/);
});
