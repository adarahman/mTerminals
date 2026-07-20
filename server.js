/**
 * ============================================================================
 *  Angel One -> Dashboard Relay
 * ============================================================================
 *  Purpose: the browser dashboard CANNOT connect to Angel One's WebSocket
 *  directly — smartapisocket.angelone.in requires custom auth headers on
 *  the socket handshake (Authorization, x-api-key, x-client-code,
 *  x-feed-token), and browsers' native WebSocket API has no way to send
 *  custom headers. Only server-side clients (this Node process, using the
 *  `ws` package) can do that.
 *
 *  So this process:
 *   1. Logs into Angel One SmartAPI (TOTP flow) and gets auth/feed tokens.
 *   2. Opens the real Angel One WebSocket, subscribes to your tokens, and
 *      decodes the binary tick packets (format per Angel One's own
 *      smartapi-python SDK).
 *   3. Separately pulls NSE's daily Participant-wise OI report and the
 *      FII/DII trade react report (these are NOT available via Angel One —
 *      they're published by NSE itself, once a day after market close).
 *   4. Re-broadcasts everything as plain JSON over a local WebSocket
 *      (ws://localhost:8081) that the dashboard's browser JS connects to
 *      with a normal `new WebSocket(url)` — no custom headers needed.
 *
 *  Run:
 *    npm install
 *    cp .env.example .env      # fill in your credentials
 *    node server.js
 * ============================================================================
 */

require('dotenv').config();
const WebSocket = require('ws');
const axios = require('axios');
const { authenticator } = require('otplib');

const {
  SMARTAPI_CLIENT_CODE,
  SMARTAPI_PIN,          // your trading PIN (not your login password, per current SmartAPI auth)
  SMARTAPI_TOTP_SECRET,   // the secret shown when you scan the QR at smartapi.SMARTAPIbroking.com/enable-totp
  SMARTAPI_KEY,       // from the "Market Feeds API" app you create on the SmartAPI developer portal
  LOCAL_RELAY_PORT = 8081,
  NSE_REFRESH_MINUTES = 30,
} = process.env;

const REST_BASE = 'https://apiconnect.angelbroking.com';
const WS_URL = 'wss://smartapisocket.angelone.in/smart-stream';

// Exchange type + mode constants (per Angel One SmartAPI WebSocket 2.0)
const EXCHANGE = { NSE_CM: 1, NSE_FO: 2, BSE_CM: 3, BSE_FO: 4, MCX_FO: 5, NCX_FO: 7, CDE_FO: 13 };
const MODE = { LTP: 1, QUOTE: 2, SNAP_QUOTE: 3, DEPTH: 4 };

// ----------------------------------------------------------------------------
// Instrument tokens you care about. NSE token numbers for indices are fixed;
// stock tokens you can look up once from Angel One's instrument master:
// https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
// ----------------------------------------------------------------------------
const WATCHLIST = {
  quotes: [
    { token: '26000',    exch: EXCHANGE.NSE_CM, label: 'NIFTY 50' },
    { token: '26009',    exch: EXCHANGE.NSE_CM, label: 'BANK NIFTY' },
    { token: '26017',    exch: EXCHANGE.NSE_CM, label: 'INDIA VIX' },
  ],
  // exampleToken values below are placeholders — replace with the real
  // tokens for each symbol from the scrip master file linked above.
  sectors: [
    { name: 'IT', stocks: [
        { n: 'INFOSYS', token: '1594', exch: EXCHANGE.NSE_CM },
        { n: 'TCS',     token: '11536', exch: EXCHANGE.NSE_CM } ] },
    { name: 'BANKING', stocks: [
        { n: 'HDFC BANK', token: '1333', exch: EXCHANGE.NSE_CM },
        { n: 'ICICI BANK', token: '4963', exch: EXCHANGE.NSE_CM } ] },
    { name: 'AUTO', stocks: [
        { n: 'MARUTI', token: '10999', exch: EXCHANGE.NSE_CM },
        { n: 'M&M',    token: '2031', exch: EXCHANGE.NSE_CM } ] },
    { name: 'ENERGY', stocks: [
        { n: 'RELIANCE', token: '2885', exch: EXCHANGE.NSE_CM },
        { n: 'ONGC',      token: '2475', exch: EXCHANGE.NSE_CM } ] },
    { name: 'METALS', stocks: [
        { n: 'TATA STEEL', token: '3499', exch: EXCHANGE.NSE_CM },
        { n: 'JSW STEEL',  token: '11723', exch: EXCHANGE.NSE_CM } ] },
    { name: 'PHARMA', stocks: [
        { n: 'SUN PHARMA', token: '3351', exch: EXCHANGE.NSE_CM },
        { n: 'DR REDDY',   token: '881', exch: EXCHANGE.NSE_CM } ] },
  ],
};

// Angel One prices are quoted in paise (price * 100). Verify this against a
// known LTP the first time you run this — divide by this constant if so.
const PRICE_DIVISOR = 100;

// ----------------------------------------------------------------------------
// 1. AUTH — login to SmartAPI, get auth_token (JWT) + feed_token
// ----------------------------------------------------------------------------
async function login() {
  const totp = authenticator.generate(SMARTAPI_TOTP_SECRET);

  const res = await axios.post(
    `${REST_BASE}/rest/auth/angelbroking/user/v1/loginByPassword`,
    { clientcode: SMARTAPI_CLIENT_CODE, password: SMARTAPI_PIN, totp },
    {
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'X-UserType': 'USER',
        'X-SourceID': 'WEB',
        'X-ClientLocalIP': '127.0.0.1',
        'X-ClientPublicIP': '127.0.0.1',
        'X-MACAddress': '00:00:00:00:00:00',
        'X-PrivateKey': SMARTAPI_KEY,
      },
    }
  );

  const { jwtToken, feedToken } = res.data.data;
  console.log('[auth] logged in, session established');
  return { authToken: jwtToken, feedToken };
}

// ----------------------------------------------------------------------------
// 2. BINARY TICK PARSER — byte-for-byte per Angel One's smartapi-python SDK
// ----------------------------------------------------------------------------
function parseTick(buf) {
  const subscriptionMode = buf.readUInt8(0);
  const exchangeType = buf.readUInt8(1);

  let token = '';
  for (let i = 2; i < 27; i++) {
    if (buf[i] === 0) break;
    token += String.fromCharCode(buf[i]);
  }

  const tick = {
    subscriptionMode,
    exchangeType,
    token,
    exchangeTimestamp: Number(buf.readBigInt64LE(35)),
    ltp: Number(buf.readBigInt64LE(43)) / PRICE_DIVISOR,
  };

  if (subscriptionMode === MODE.QUOTE || subscriptionMode === MODE.SNAP_QUOTE) {
    tick.volume = Number(buf.readBigInt64LE(67));
    tick.open = Number(buf.readBigInt64LE(91)) / PRICE_DIVISOR;
    tick.high = Number(buf.readBigInt64LE(99)) / PRICE_DIVISOR;
    tick.low = Number(buf.readBigInt64LE(107)) / PRICE_DIVISOR;
    tick.close = Number(buf.readBigInt64LE(115)) / PRICE_DIVISOR;
  }
  if (subscriptionMode === MODE.SNAP_QUOTE) {
    tick.openInterest = Number(buf.readBigInt64LE(131));
  }
  return tick;
}

// ----------------------------------------------------------------------------
// 3. ANGEL ONE WEBSOCKET — connect, subscribe, decode, keep a live cache
// ----------------------------------------------------------------------------
const liveCache = new Map(); // token -> latest tick

function connectAngelOne(authToken, feedToken) {
  const ws = new WebSocket(WS_URL, {
    headers: {
      Authorization: `Bearer ${authToken}`,
      'x-api-key': SMARTAPI_KEY,
      'x-client-code': SMARTAPI_CLIENT_CODE,
      'x-feed-token': feedToken,
    },
  });

  ws.on('open', () => {
    console.log('[angelone] websocket connected');

    const allTokens = [
      ...WATCHLIST.quotes.map(q => ({ ...q })),
      ...WATCHLIST.sectors.flatMap(s => s.stocks),
    ];
    const byExchange = {};
    allTokens.forEach(t => {
      byExchange[t.exch] = byExchange[t.exch] || [];
      byExchange[t.exch].push(t.token);
    });
    const tokenList = Object.entries(byExchange).map(([exchangeType, tokens]) => ({
      exchangeType: Number(exchangeType),
      tokens,
    }));

    ws.send(JSON.stringify({
      correlationID: 'dashboard1',
      action: 1, // subscribe
      params: { mode: MODE.SNAP_QUOTE, tokenList },
    }));

    // Angel One expects a "ping" text heartbeat periodically
    ws._hb = setInterval(() => { if (ws.readyState === ws.OPEN) ws.send('ping'); }, 10000);
  });

  ws.on('message', (data) => {
    if (data.toString() === 'pong') return;
    if (!Buffer.isBuffer(data) || data.length < 51) return; // control/heartbeat frame
    try {
      const tick = parseTick(data);
      liveCache.set(tick.token, tick);
      broadcastLiveUpdate();
    } catch (e) {
      console.error('[angelone] parse error', e.message);
    }
  });

  ws.on('close', () => {
    clearInterval(ws._hb);
    console.log('[angelone] disconnected, retrying in 5s');
    setTimeout(() => connectAngelOne(authToken, feedToken), 5000);
  });
  ws.on('error', (e) => console.error('[angelone] error', e.message));

  return ws;
}

// Map live ticks back onto the dashboard's expected {quotes, sectors} shape
function buildLivePayload() {
  const tickFor = (token) => liveCache.get(token);

  const quotes = WATCHLIST.quotes.map(q => {
    const t = tickFor(q.token);
    if (!t) return null;
    const chg = t.ltp - t.close;
    const pct = t.close ? (chg / t.close) * 100 : 0;
    return {
      label: q.label,
      val: t.ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 }),
      chg: (chg >= 0 ? '+' : '') + chg.toFixed(2),
      pct: (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%',
      dir: chg >= 0 ? 'up' : 'down',
    };
  }).filter(Boolean);

  const sectors = WATCHLIST.sectors.map(s => ({
    name: s.name,
    tag: '—',      // buildup classification needs OI-change logic; left as-is here
    cls: 'tag-neutral',
    stocks: s.stocks.map(st => {
      const t = tickFor(st.token);
      if (!t) return { n: st.n, v: '—', dir: 'flat' };
      const chg = t.ltp - t.close;
      const pct = t.close ? (chg / t.close) * 100 : 0;
      return { n: st.n, v: (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%', dir: pct >= 0 ? 'up' : 'down' };
    }),
  }));

  return { quotes, sectors };
}

// ----------------------------------------------------------------------------
// 4. NSE EOD REPORTS — participant-wise OI + FII/DII trade activity
//    These are NOT Angel One data. NSE publishes them once a day (typically
//    5:30–6:30pm IST) and blocks non-browser requests, so we bootstrap a
//    session cookie against the homepage first, same as any NSE scraper must.
// ----------------------------------------------------------------------------
let nseCookieJar = '';

async function bootstrapNseSession() {
  const res = await axios.get('https://www.nseindia.com', {
    headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
  });
  nseCookieJar = res.headers['set-cookie']?.join('; ') || '';
}

async function fetchFiiDii() {
  const res = await axios.get('https://www.nseindia.com/api/fiidiiTradeReact', {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
      Referer: 'https://www.nseindia.com/',
      Cookie: nseCookieJar,
    },
  });
  // res.data: [{category:'FII/FPI', date, buyValue, sellValue, netValue}, {category:'DII', ...}]
  return res.data;
}

async function fetchParticipantOI() {
  const today = new Date();
  const dd = String(today.getDate()).padStart(2, '0');
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const yyyy = today.getFullYear();
  const url = `https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_${dd}${mm}${yyyy}.csv`;

  const res = await axios.get(url, {
    headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', Cookie: nseCookieJar },
  });
  return parseParticipantOiCsv(res.data);
}

function parseParticipantOiCsv(csvText) {
  const lines = csvText.trim().split('\n').slice(1); // skip title row
  const header = lines[0].split(',');
  const rows = lines.slice(1).map(l => l.split(','));

  const longIdx = header.findIndex(h => h.includes('Future Index Long'));
  const shortIdx = header.findIndex(h => h.includes('Future Index Short'));

  const out = {};
  rows.forEach(r => {
    const name = (r[0] || '').trim();
    if (!name) return;
    const long = Number(r[longIdx]) || 0;
    const short = Number(r[shortIdx]) || 0;
    out[name] = { long, short, total: long + short };
  });
  return out; // { Client:{}, DII:{}, FII:{}, Pro:{}, TOTAL:{} }
}

async function refreshNseData() {
  try {
    if (!nseCookieJar) await bootstrapNseSession();
    const [fiiDii, participantOi] = await Promise.all([fetchFiiDii(), fetchParticipantOI()]);

    const fii = fiiDii.find(r => /FII|FPI/i.test(r.category));
    const dii = fiiDii.find(r => /DII/i.test(r.category));

    const totalIndexOi = Object.values(participantOi)
      .filter(v => v && typeof v.total === 'number')
      .reduce((sum, v) => sum + v.total, 0) || 1;

    const oi = ['FII', 'Pro', 'Client', 'DII'].map(name => {
      const row = participantOi[name] || { long: 0, short: 0, total: 0 };
      return {
        name: name.toUpperCase(),
        pct: Math.round((row.total / totalIndexOi) * 1000) / 10,
        color: { FII: 'var(--violet)', Pro: 'var(--amber)', Client: 'var(--grey)', DII: 'var(--green)' }[name],
        trend: row.long >= row.short ? 'LONG BUILD' : 'SHORT BUILD',
        dir: row.long >= row.short ? 'up' : 'down',
      };
    });

    const fiiRow = participantOi['FII'];
    const ratio = fiiRow ? Math.round((fiiRow.long / (fiiRow.long + fiiRow.short)) * 1000) / 10 : null;

    nseCache = {
      flow: null, // fill from fiiDii buy/sell values if you want the 30D series charted;
                  // fiiDiiTradeReact only returns the latest session, so a real 30D
                  // series means persisting a daily snapshot yourself (e.g. append
                  // to a small local JSON/SQLite each evening after this runs).
      ratio,
      oi,
      _fiiDiiLatest: { fii, dii },
    };

    console.log('[nse] refreshed EOD participant/FII-DII data');
    broadcastNseUpdate();
  } catch (e) {
    console.error('[nse] refresh failed —', e.message,
      '(NSE blocks non-browser traffic; if this keeps failing, run this fetch from a machine/IP that can browse nseindia.com normally, or use a paid data vendor instead)');
  }
}

let nseCache = null;

// ----------------------------------------------------------------------------
// 4b. NSE ALL-INDICES SNAPSHOT — ported from market_api.py's fetch_all_indices()
//     (see README's "market_api.py now used only for fetch_all_indices()"
//     note). Unlike the EOD reports above (once/day, published by NSE after
//     close), this hits NSE's live equity-stock-indices / equity-stockIndex
//     endpoints — same session-cookie approach, but its own session object
//     (ensureIdxSession()/nseIdxRequest() below) because market_api.py's
//     version auto-re-warms on 401/403 and retries connection errors with
//     backoff, which the EOD-report fetchers above don't do. Feeds:
//       - per-stock rows (incl. ffmc, free-float market cap) for every
//         index in DEFAULT_INDICES
//       - Top Drivers/Draggers: each stock's ffmc-weighted contribution to
//         its index's % change, ranked — same formula as market_api.py's
//         parse_index_records() comment: weight_i = ffmc_i / sum(ffmc) for
//         stocks sharing the same "Index" tag. The actual ranking/output
//         function itself lives in option_chain_json.py, not market_api.py,
//         and wasn't provided — this is a port of the formula as described
//         in that comment, not a copy of that file's exact output shape.
// ----------------------------------------------------------------------------

const FNO_STOCK_INDEX = 'SECURITIES IN F&O';

const INDEX_RENAME = {
  'NIFTY 50': 'NIFTY',
  'NIFTY NEXT 50': 'NIFTYNEXT50',
  'NIFTY BANK': 'BANKNIFTY',
  'NIFTY FINANCIAL SERVICES': 'FINNIFTY',
  'NIFTY FIN SERVICE': 'FINNIFTY',
  'NIFTY MIDCAP SELECT': 'MIDCPNIFTY',
};

const DEFAULT_INDICES = [
  'NIFTY 50', 'NIFTY BANK', 'NIFTY MIDCAP SELECT',
  'NIFTY FIN SERVICE', 'NIFTY NEXT 50',
  FNO_STOCK_INDEX,
];

const DF_IDX_TTL_MS = 20 * 1000; // matches README's "20s-cached"
const NSE_SESSION_TTL_MS = 18 * 60 * 1000;

const BASE_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
  'Accept-Language': 'en-IN,en;q=0.9',
  'Cache-Control': 'no-cache',
  Pragma: 'no-cache',
};

let idxCookieJar = '';
let idxSessionExpiry = 0;

async function ensureIdxSession(force = false) {
  if (!force && idxCookieJar && Date.now() < idxSessionExpiry) return;

  const warmUpUrls = [
    ['https://www.nseindia.com/', {}],
    ['https://www.nseindia.com/option-chain', { Referer: 'https://www.nseindia.com/' }],
    ['https://www.nseindia.com/market-data/live-equity-market', { Referer: 'https://www.nseindia.com/' }],
  ];

  let cookies = [];
  for (const [url, extra] of warmUpUrls) {
    try {
      const res = await axios.get(url, { headers: { ...BASE_HEADERS, ...extra }, timeout: 20000 });
      const setCookie = res.headers['set-cookie'];
      if (setCookie) cookies = cookies.concat(setCookie);
    } catch (e) {
      console.error(`[ensureIdxSession] warm-up failed for ${url}: ${e.message}`);
    }
  }
  // Each warm-up hop can set new cookies (Akamai in particular rotates them
  // per hop) — concatenating all Set-Cookie headers across hops, not just
  // the last one, mirrors requests.Session()'s cumulative cookie jar.
  if (cookies.length) idxCookieJar = cookies.map(c => c.split(';')[0]).join('; ');
  idxSessionExpiry = Date.now() + NSE_SESSION_TTL_MS;
}

function idxApiHeaders(referer) {
  const h = {
    ...BASE_HEADERS,
    Accept: 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
    Cookie: idxCookieJar,
  };
  if (referer) h.Referer = referer;
  return h;
}

// GET an NSE JSON endpoint; auto re-warms + retries once on 401/403, and
// retries up to connRetries times (1.5s, then 3s backoff) on connection-level
// errors — same two-tier retry as market_api.py's nse_request().
async function nseIdxRequest(url, referer = '', retried = false, connRetries = 2) {
  await ensureIdxSession();
  let res;
  try {
    res = await axios.get(url, { headers: idxApiHeaders(referer), timeout: 20000, validateStatus: () => true });
  } catch (e) {
    if (connRetries > 0) {
      const wait = 1.5 * (3 - connRetries) * 1000;
      console.log(`[nseIdxRequest] request error: ${e.message} — retrying in ${wait / 1000}s (${connRetries} left)`);
      await new Promise(r => setTimeout(r, wait));
      return nseIdxRequest(url, referer, retried, connRetries - 1);
    }
    console.log(`[nseIdxRequest] request error: ${e.message} — out of retries`);
    return null;
  }

  if ((res.status === 401 || res.status === 403) && !retried) {
    console.log(`[nseIdxRequest] HTTP ${res.status} — re-warming session and retrying`);
    await ensureIdxSession(true);
    return nseIdxRequest(url, referer, true, connRetries);
  }
  if (res.status !== 200) {
    console.log(`[nseIdxRequest] HTTP ${res.status} for ${url}`);
    return null;
  }
  return res.data;
}

function cacheBust(url) {
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}_=${Date.now()}`;
}

function buildFnoUrl(indexName) {
  const encoded = encodeURIComponent(indexName);
  const url = indexName.trim().toUpperCase() === FNO_STOCK_INDEX
    ? `https://www.nseindia.com/api/equity-stockIndex?index=${encoded}`
    : `https://www.nseindia.com/api/equity-stock-indices?index=${encoded}`;
  return cacheBust(url);
}

function fetchFnoIndex(indexName) {
  return nseIdxRequest(buildFnoUrl(indexName), 'https://www.nseindia.com/market-data/live-equity-market');
}

function renameIndex(symbol) {
  return INDEX_RENAME[symbol.trim().toUpperCase()] || symbol;
}

// Mirrors market_api.py's parse_index_records() — one row per constituent
// stock (plus the index's own self-row, series=null), including ffmc for
// the driver/dragger weighting below.
function parseIndexRecords(jsonData, indexLabel) {
  if (!jsonData) return [];
  const rows = [];
  for (const rec of jsonData.data || []) {
    const symbol = String(rec.symbol || '').trim();
    if (!symbol) continue;
    rows.push({
      Index: indexLabel,
      Symbol: renameIndex(symbol),
      Series: rec.series ?? null, // self-row (index's own quote) has series=null
      LastPrice: rec.lastPrice,
      PrevClose: rec.previousClose,
      Change: rec.change,
      PctChange: rec.pChange,
      ffmc: rec.ffmc, // free-float market cap — used for driver/dragger weight below
    });
  }
  return rows;
}

// Port of fetch_all_indices(): threaded (here, Promise.all-parallel) fetch
// across every index in `indices`, flattened into one row array.
async function fetchAllIndices(indices = DEFAULT_INDICES) {
  const results = await Promise.all(indices.map(async (idx) => {
    try {
      const data = await fetchFnoIndex(idx);
      return data ? parseIndexRecords(data, idx) : [];
    } catch (e) {
      console.error(`[fetchAllIndices] error for ${idx}: ${e.message}`);
      return [];
    }
  }));
  return results.flat();
}

// weight_i = ffmc_i / sum(ffmc) for stocks sharing the same Index tag, then
// rank by weight * %Change (each stock's ffmc-weighted contribution to its
// index's move) — top N positive = Drivers, top N negative = Draggers.
function computeTopDriversDraggers(rows, indexLabel = FNO_STOCK_INDEX, topN = 3) {
  const stocks = rows.filter(r => r.Index === indexLabel && r.Series !== null && r.ffmc);
  const totalFfmc = stocks.reduce((sum, r) => sum + (Number(r.ffmc) || 0), 0) || 1;

  const contributions = stocks.map(r => {
    const weight = (Number(r.ffmc) || 0) / totalFfmc;
    return {
      symbol: r.Symbol,
      pctChange: Number(r.PctChange) || 0,
      weight,
      contribution: weight * (Number(r.PctChange) || 0),
    };
  });

  const sorted = contributions.slice().sort((a, b) => b.contribution - a.contribution);
  return {
    drivers: sorted.slice(0, topN),
    draggers: sorted.slice(-topN).reverse(),
  };
}

let idxCache = null; // { rows, driversDraggers, fetchedAt }

async function refreshAllIndices() {
  try {
    const rows = await fetchAllIndices(DEFAULT_INDICES);
    if (!rows.length) {
      console.log('[refreshAllIndices] no rows returned — skipping cache update');
      return;
    }
    idxCache = {
      rows,
      driversDraggers: computeTopDriversDraggers(rows),
      fetchedAt: Date.now(),
    };
    console.log(`[nse] all-indices snapshot refreshed — ${rows.length} rows`);
    broadcast({ driversDraggers: idxCache.driversDraggers });
  } catch (e) {
    console.error('[refreshAllIndices] failed —', e.message);
  }
}

// ----------------------------------------------------------------------------
// 5. LOCAL RELAY — plain WebSocket the browser dashboard connects to
// ----------------------------------------------------------------------------
const relay = new WebSocket.Server({ port: LOCAL_RELAY_PORT });
console.log(`[relay] listening on ws://localhost:${LOCAL_RELAY_PORT}`);

function broadcastLiveUpdate() {
  const payload = buildLivePayload();
  broadcast(payload);
}
function broadcastNseUpdate() {
  if (!nseCache) return;
  broadcast({ ratio: nseCache.ratio, oi: nseCache.oi });
}
function broadcast(payload) {
  const msg = JSON.stringify(payload);
  relay.clients.forEach(c => { if (c.readyState === WebSocket.OPEN) c.send(msg); });
}

relay.on('connection', (client) => {
  console.log('[relay] dashboard connected');
  // send whatever we have immediately so the UI isn't blank on load
  if (liveCache.size) client.send(JSON.stringify(buildLivePayload()));
  if (nseCache) client.send(JSON.stringify({ ratio: nseCache.ratio, oi: nseCache.oi }));
  if (idxCache) client.send(JSON.stringify({ driversDraggers: idxCache.driversDraggers }));
});

// ----------------------------------------------------------------------------
// BOOT
// ----------------------------------------------------------------------------
(async () => {
  const { authToken, feedToken } = await login();
  connectAngelOne(authToken, feedToken);

  refreshNseData();
  setInterval(refreshNseData, NSE_REFRESH_MINUTES * 60 * 1000);

  refreshAllIndices();
  setInterval(refreshAllIndices, DF_IDX_TTL_MS);
})().catch(err => {
  console.error('[boot] failed to start —', err.response?.data || err.message);
  process.exit(1);
});
